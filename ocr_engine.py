from pathlib import Path
from paddleocr import PaddleOCR
import fitz
import json
import re
import time
import tempfile
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

BASE_DIR = Path(__file__).resolve().parent
def find_input_file():
    for name in ("conta_001.pdf", "conta_001.jpg", "conta_001.jpeg", "conta_001.png"):
        candidate = BASE_DIR / "images" / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Coloque em images/ um arquivo chamado conta_001.pdf, conta_001.jpg, "
        "conta_001.jpeg ou conta_001.png."
    )

OUTPUT_DIR = Path(tempfile.gettempdir()) / "cemig_ocr_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# v0.41 - ROUTER VISUAL BARATO (SEM OCR) + ESCOLHA DE MOTOR
# ----------------------------------------------------------------------
# A v0.19/v0.20 permanece como referência de precisão.
# Nesta versão só reduzimos imagens raster muito grandes, comuns em fotos
# de celular. Imagens de referência menores que este limite passam intactas.
MAX_RASTER_DIMENSION = 1800



def normalize_text(value: str) -> str:
    return " ".join(str(value).strip().split())


def parse_money(text: str):
    # Aceita valores como 105,96 / 1.299,70 / R$49.447,26 / 1.299,70-
    m = re.search(
        r"(?:R\$)?\s*(-?\d{1,3}(?:\.\d{3})*|\d+)\,([0-9]{2})(-?)",
        text.strip(),
        re.IGNORECASE
    )
    if not m:
        return None

    raw = m.group(0).strip()
    inteiro = m.group(1).replace(".", "")
    centavos = m.group(2)

    return {
        "raw": raw,
        "value": float(f"{inteiro}.{centavos}")
    }


def find_irpj(texts):
    """
    Extrai o valor de 'Imposto Retido - IRPJ'.

    Retorna None quando o documento não apresenta IRPJ.
    O valor é armazenado como magnitude positiva:
    '1.299,70-' -> 1299.70
    """
    anchors = (
        "IMPOSTO RETIDO - IRPJ",
        "IMPOSTO RETIDO – IRPJ",
        "IMPOSTO RETIDO IRPJ",
        "IRPJ",
    )

    for i, text in enumerate(texts):
        upper = text.upper()

        if not any(anchor in upper for anchor in anchors):
            continue

        # Rótulo e valor podem vir na mesma região OCR.
        parsed = parse_money(text)
        if parsed:
            return abs(parsed["value"])

        # Ou o OCR pode separar o valor em uma região logo após o rótulo.
        for j in range(i + 1, min(i + 5, len(texts))):
            parsed = parse_money(texts[j])
            if parsed:
                return abs(parsed["value"])

    return None


def find_money_after_anchor(texts, anchor, max_distance=8):
    anchor_upper = anchor.upper()
    for i, t in enumerate(texts):
        if anchor_upper in t.upper():
            for j in range(i + 1, min(i + 1 + max_distance, len(texts))):
                parsed = parse_money(texts[j])
                if parsed:
                    return parsed
    return None


def find_date_after_anchor(texts, anchor, max_distance=8):
    anchor_upper = anchor.upper()
    for i, t in enumerate(texts):
        if anchor_upper in t.upper():
            for j in range(i + 1, min(i + 1 + max_distance, len(texts))):
                if re.fullmatch(r"\d{2}/\d{2}/\d{4}", texts[j]):
                    return texts[j]
    return None


def find_reference_near_top(texts):
    for t in texts[:40]:
        if re.fullmatch(r"[A-Z]{3}/\d{4}", t.upper()):
            return t.upper()
    return None


def parse_installation(texts):
    for i, t in enumerate(texts):
        u = t.upper()
        if "INSTALAÇÃO" in u and ("N°" in u or "Nº" in u):
            for j in range(i + 1, min(i + 6, len(texts))):
                if re.fullmatch(r"\d{8,12}", texts[j]):
                    return texts[j]
    return None


def find_customer_name_index(texts):
    for i, t in enumerate(texts[:40]):
        if re.fullmatch(r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ ]{8,}", t) and not any(
            x in t.upper()
            for x in [
                "CEMIG",
                "TARIFA",
                "NOTA FISCAL",
                "VALOR",
                "VENCIMENTO",
                "REFERENTE",
                "AV.",
                "CEP"
            ]
        ):
            return i
    return None


def is_noise_between_address_lines(text):
    u = text.upper()
    noise_tokens = [
        "NOTA FISCAL",
        "DATA DE EMISSÃO",
        "CPF",
        "CONSULTE",
        "CHAVE DE ACESSO",
        "PROTOCOLO",
        "INSTALAÇÃO",
        "HTTP://",
        "HTTPS://",
    ]
    return any(token in u for token in noise_tokens)


def looks_like_header_noise(text):
    u = text.upper()
    noise = [
        "DOCUMENTO AUXILIAR",
        "NOTA FISCAL",
        "ENERGIA ELÉTRICA",
        "ENERGIA ELETRICA",
        "CEMIG",
        "VALOR A PAGAR",
        "VENCIMENTO",
        "REFERENTE A",
        "UNIDADE CONSUMIDORA",
        "PROTOCOLO",
        "CHAVE DE ACESSO",
        "DATA DE EMISSÃO",
    ]
    return any(token in u for token in noise)


def find_name_fallback(texts):
    """
    Fallback para o layout digital.
    Só é usado quando o nome retornado pela regra antiga parece ruído de cabeçalho.
    Procura uma linha textual imediatamente antes de um endereço plausível.
    """
    for i in range(1, min(len(texts), 80)):
        t = texts[i]
        if looks_like_header_noise(t):
            continue

        # procura uma linha de endereço logo abaixo
        for j in range(i + 1, min(i + 4, len(texts))):
            address_line = texts[j]
            if re.match(r"^[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ0-9 .'-]+\s+\d+(?:\s+.*)?$", address_line.upper()):
                candidate = t.strip()
                if (
                    len(candidate) >= 8
                    and not re.search(r"\d", candidate)
                    and not looks_like_header_noise(candidate)
                ):
                    return candidate
    return None


def parse_customer_address_fallback(texts, name_idx):
    """
    Fallback do layout digital.
    Mantém a estrutura:
      nome
      logradouro + número + complemento
      bairro/localidade
      CEP + cidade + UF

    Só deve ser adotado se o parser legado vier incompleto ou claramente incorreto.
    """
    result = {
        "logradouro": None, "numero": None, "complemento": None,
        "bairro": None, "cep": None, "cidade": None, "uf": None,
    }

    if name_idx is None:
        return result

    window = texts[name_idx + 1:min(name_idx + 15, len(texts))]

    street_idx = None
    for idx, t in enumerate(window):
        if looks_like_header_noise(t):
            continue
        m = re.match(r"(.+?)\s+(\d+)(?:\s+(.*))?$", t.strip())
        if m:
            result["logradouro"] = m.group(1).strip()
            result["numero"] = m.group(2)
            result["complemento"] = (m.group(3) or "").strip() or None
            street_idx = idx
            break

    cep_idx = None
    for idx, t in enumerate(window):
        m = re.search(r"(\d{5}-\d{3})\s+(.+?)[,\s]+([A-Z]{2})$", t.upper())
        if m:
            result["cep"] = m.group(1)
            result["cidade"] = m.group(2).strip(" ,")
            result["uf"] = m.group(3)
            cep_idx = idx
            break

    if street_idx is not None and cep_idx is not None and cep_idx > street_idx:
        for idx in range(street_idx + 1, cep_idx):
            candidate = window[idx].strip()
            if (
                candidate
                and not looks_like_header_noise(candidate)
                and not re.search(r"\d{5}-\d{3}", candidate)
                and not parse_money(candidate)
            ):
                result["bairro"] = candidate
                break

    return result


def address_score(address):
    fields = ("logradouro", "numero", "bairro", "cep", "cidade", "uf")
    return sum(1 for field in fields if address.get(field))


def merge_name_and_address(texts):
    """
    Regra de compatibilidade:
    - mantém parser legado se ele produzir um resultado bom;
    - usa fallback apenas quando houver ganho claro.
    """
    legacy_name_idx = find_customer_name_index(texts)
    legacy_name = texts[legacy_name_idx] if legacy_name_idx is not None else None
    legacy_address = parse_customer_address(texts, legacy_name_idx)

    legacy_name_bad = (
        legacy_name is None
        or looks_like_header_noise(legacy_name)
        or len(legacy_name) > 70
    )

    if not legacy_name_bad and address_score(legacy_address) >= 5:
        return legacy_name, legacy_address

    fallback_name = find_name_fallback(texts)
    if fallback_name:
        try:
            fallback_idx = texts.index(fallback_name)
        except ValueError:
            fallback_idx = None
        fallback_address = parse_customer_address_fallback(texts, fallback_idx)

        if address_score(fallback_address) > address_score(legacy_address):
            return fallback_name, fallback_address

    return legacy_name, legacy_address

def parse_customer_address(texts, name_idx):
    result = {
        "logradouro": None,
        "numero": None,
        "complemento": None,
        "bairro": None,
        "cep": None,
        "cidade": None,
        "uf": None,
    }

    if name_idx is None:
        return result

    start = name_idx + 1
    end = min(name_idx + 12, len(texts))
    window = texts[start:end]

    city_rel_idx = None

    for rel_idx, t in enumerate(window):
        m = re.match(
            r".*?(\d{5}-\d{3})\s+(.+?)[,\s]+([A-Z]{2})$",
            t.upper()
        )
        if m:
            city_rel_idx = rel_idx
            result["cep"] = m.group(1)
            result["cidade"] = m.group(2).strip(" ,")
            result["uf"] = m.group(3)
            break

    street_rel_idx = None
    search_until = city_rel_idx if city_rel_idx is not None else len(window)

    for rel_idx, t in enumerate(window[:search_until]):
        if is_noise_between_address_lines(t):
            continue

        m = re.match(r"(.+?)\s+(\d+)(?:\s+(.*))?$", t)

        if m:
            street_rel_idx = rel_idx
            result["logradouro"] = m.group(1).strip()
            result["numero"] = m.group(2)
            result["complemento"] = (m.group(3) or "").strip() or None
            break

    if street_rel_idx is not None and city_rel_idx is not None:
        candidates = []

        for rel_idx in range(street_rel_idx + 1, city_rel_idx):
            candidate = window[rel_idx]

            if is_noise_between_address_lines(candidate):
                continue

            if re.search(r"\d{5}-\d{3}", candidate):
                continue

            if re.fullmatch(r"(?:R\$)?\s*-?\d+,\d{2}", candidate):
                continue

            candidates.append(candidate)

        if candidates:
            result["bairro"] = candidates[0]

    return result


def normalize_barcode_candidate(text: str) -> str:
    cleaned = re.sub(r"[^0-9\-\s]", "", text)
    return normalize_text(cleaned)


def is_full_linha_digitavel(text: str) -> bool:
    pattern = re.compile(
        r"^\d{11}-\d\s+\d{11}-\d\s+\d{11}-\d\s+\d{11}-\d$"
    )
    return bool(pattern.fullmatch(normalize_barcode_candidate(text)))


def find_barcode_line(texts):
    for t in texts:
        candidate = normalize_barcode_candidate(t)
        if is_full_linha_digitavel(candidate):
            return candidate

    for i in range(len(texts) - 1):
        candidate = normalize_barcode_candidate(
            f"{texts[i]} {texts[i + 1]}"
        )
        if is_full_linha_digitavel(candidate):
            return candidate

    for i in range(len(texts) - 2):
        candidate = normalize_barcode_candidate(
            f"{texts[i]} {texts[i + 1]} {texts[i + 2]}"
        )
        if is_full_linha_digitavel(candidate):
            return candidate

    return None


# ----------------------------------------------------------------------
# FEBRABAN - ARRECADAÇÃO
# ----------------------------------------------------------------------

def febraban_mod10(number: str) -> int:
    """
    Módulo 10:
    multiplicadores 2,1 da direita para a esquerda.
    Soma dos algarismos dos produtos.
    DV = (10 - (soma % 10)) % 10
    """
    total = 0
    weight = 2

    for char in reversed(number):
        product = int(char) * weight
        total += (product // 10) + (product % 10)
        weight = 1 if weight == 2 else 2

    return (10 - (total % 10)) % 10


def febraban_mod11(number: str) -> int:
    """
    Módulo 11 para arrecadação:
    multiplicadores 2..9 da direita para a esquerda.

    Regra FEBRABAN:
    resto 0 ou 1 -> DV 0
    resultado 10 -> DV 1
    demais -> 11 - resto
    """
    total = 0
    weight = 2

    for char in reversed(number):
        total += int(char) * weight
        weight += 1
        if weight > 9:
            weight = 2

    remainder = total % 11

    if remainder in (0, 1):
        return 0

    dv = 11 - remainder

    if dv == 10:
        return 1

    return dv


def validate_febraban_linha_digitavel(line: str):
    """
    Valida documento de arrecadação FEBRABAN com 48 dígitos.

    Linha digitável:
      4 blocos de 12 posições
      cada bloco = 11 dados + 1 DV

    Código de barras:
      44 dígitos
      obtido removendo o DV de cada bloco.

    O terceiro dígito define:
      6 -> valor efetivo / módulo 10
      7 -> referência     / módulo 10
      8 -> valor efetivo / módulo 11
      9 -> referência     / módulo 11
    """
    if not line:
        return {
            "valido": False,
            "erro": "Linha digitável não encontrada."
        }

    digits = re.sub(r"\D", "", line)

    if len(digits) != 48:
        return {
            "valido": False,
            "erro": f"Linha digitável possui {len(digits)} dígitos; esperado: 48."
        }

    blocks = [digits[i:i + 12] for i in range(0, 48, 12)]

    barcode = "".join(block[:11] for block in blocks)

    if len(barcode) != 44:
        return {
            "valido": False,
            "erro": "Falha ao reconstruir código de barras de 44 posições."
        }

    produto = barcode[0]
    segmento = barcode[1]
    referencia = barcode[2]
    dv_geral_informado = int(barcode[3])

    if produto != "8":
        return {
            "valido": False,
            "erro": f"Produto inválido para arrecadação: {produto}."
        }

    if referencia not in ("6", "7", "8", "9"):
        return {
            "valido": False,
            "erro": f"Identificador de valor/referência inválido: {referencia}."
        }

    calc_dv = febraban_mod10 if referencia in ("6", "7") else febraban_mod11
    modulo = 10 if referencia in ("6", "7") else 11

    validacao_blocos = []
    blocos_validos = True

    for index, block in enumerate(blocks, start=1):
        dados = block[:11]
        dv_informado = int(block[11])
        dv_calculado = calc_dv(dados)
        valido = dv_informado == dv_calculado

        if not valido:
            blocos_validos = False

        validacao_blocos.append({
            "bloco": index,
            "dados": dados,
            "dv_informado": dv_informado,
            "dv_calculado": dv_calculado,
            "valido": valido
        })

    # O DV geral fica na 4ª posição do código de barras e é calculado
    # sobre as demais 43 posições.
    barcode_sem_dv_geral = barcode[:3] + barcode[4:]
    dv_geral_calculado = calc_dv(barcode_sem_dv_geral)
    dv_geral_valido = dv_geral_informado == dv_geral_calculado

    # Campo de valor: posições 5 a 15 (11 posições), em centavos.
    valor_codigo = None
    valor_efetivo = referencia in ("6", "8")

    if valor_efetivo:
        valor_raw = barcode[4:15]
        valor_codigo = int(valor_raw) / 100.0

    segmento_descricao = {
        "1": "Prefeituras",
        "2": "Saneamento",
        "3": "Energia Elétrica e Gás",
        "4": "Telecomunicações",
        "5": "Órgãos Governamentais",
        "6": "Carnês e Assemelhados / CNPJ",
        "7": "Multas de Trânsito",
        "9": "Uso exclusivo do banco",
    }.get(segmento, "Segmento desconhecido")

    valido = blocos_validos and dv_geral_valido

    return {
        "valido": valido,
        "erro": None if valido else "Um ou mais dígitos verificadores não conferem.",
        "produto": produto,
        "segmento": segmento,
        "segmento_descricao": segmento_descricao,
        "identificador_valor_referencia": referencia,
        "valor_efetivo": valor_efetivo,
        "modulo": modulo,
        "codigo_barras_44": barcode,
        "dv_geral_informado": dv_geral_informado,
        "dv_geral_calculado": dv_geral_calculado,
        "dv_geral_valido": dv_geral_valido,
        "blocos": validacao_blocos,
        "valor": valor_codigo
    }


def is_digital_layout(texts):
    return any("UNIDADE CONSUMIDORA" in t.upper() for t in texts)


def box_center_y(box):
    try:
        # rec_boxes can be [x1,y1,x2,y2]; dt_polys can be 4 points.
        if len(box) == 4 and not hasattr(box[0], "__len__"):
            return (float(box[1]) + float(box[3])) / 2.0
        ys = [float(pt[1]) for pt in box]
        return sum(ys) / len(ys)
    except Exception:
        return None


def parse_digital_customer(texts, boxes):
    """
    Parser cadastral específico do layout digital.

    Regra central:
    - localiza a linha 'N.º DA UNIDADE CONSUMIDORA';
    - o bloco do consumidor fica acima dessa âncora e abaixo do cabeçalho CEMIG;
    - evita REIMPRESSÃO e endereço institucional da CEMIG.
    """
    uc_idx = next(
        (i for i, t in enumerate(texts) if "UNIDADE CONSUMIDORA" in t.upper()),
        None
    )
    if uc_idx is None:
        return None, None

    # Se houver geometria, restringe aos elementos visualmente acima da UC.
    uc_y = box_center_y(boxes[uc_idx]) if boxes is not None and uc_idx < len(boxes) else None

    candidates = []
    for i in range(uc_idx):
        t = texts[i].strip()
        if not t:
            continue
        y = box_center_y(boxes[i]) if boxes is not None and i < len(boxes) else None
        if uc_y is not None and y is not None and y >= uc_y:
            continue
        candidates.append((i, t, y))

    # Remove explicitamente o cabeçalho institucional.
    filtered = []
    for item in candidates:
        u = item[1].upper()
        if any(token in u for token in (
            "REIMPRESSÃO", "DOCUMENTO AUXILIAR", "CEMIG DISTRIBUI",
            "AV. BARBACENA", "BARBACENA", "BELO HORIZONTE",
            "INSC. ESTADUAL", "CNPJ", "CEP:30190-131"
        )):
            continue
        if any(token in u for token in (
            "REFERENTE A", "VENCIMENTO", "VALOR A PAGAR",
            "NOTA FISCAL", "DATA DE EMISSÃO", "CHAVE DE ACESSO",
            "PROTOCOLO", "CONSULTE"
        )):
            continue
        filtered.append(item)

    # CEP do cliente é uma âncora forte. A partir dele, voltamos para endereço/nome.
    cep_pos = None
    cep_match = None
    for pos, (_, t, _) in enumerate(filtered):
        m = re.search(r"(\d{5}-\d{3})\s+(.+?)[,\s]+([A-Z]{2})$", t.upper())
        if m:
            cep_pos = pos
            cep_match = m
            break

    if cep_pos is None:
        return None, None

    address = {
        "logradouro": None, "numero": None, "complemento": None,
        "bairro": None, "cep": cep_match.group(1),
        "cidade": cep_match.group(2).strip(" ,"),
        "uf": cep_match.group(3),
    }

    # Busca logradouro nas linhas anteriores ao CEP.
    street_pos = None
    for pos in range(cep_pos - 1, -1, -1):
        t = filtered[pos][1]
        m = re.match(r"(.+?)\s+(\d+)(?:\s+(.*))?$", t)
        if m:
            address["logradouro"] = m.group(1).strip()
            address["numero"] = m.group(2)
            address["complemento"] = (m.group(3) or "").strip() or None
            street_pos = pos
            break

    if street_pos is None:
        return None, None

    # Bairro/localidade fica entre endereço e CEP.
    for pos in range(street_pos + 1, cep_pos):
        t = filtered[pos][1].strip()
        if t and not re.search(r"\d", t) and not looks_like_header_noise(t):
            address["bairro"] = t
            break

    # Nome: primeira linha textual útil imediatamente anterior ao endereço.
    nome = None
    for pos in range(street_pos - 1, -1, -1):
        t = filtered[pos][1].strip()
        u = t.upper()
        if (
            len(t) >= 5
            and not re.search(r"\d", t)
            and "CPF" not in u
            and not looks_like_header_noise(t)
        ):
            nome = t
            break

    return nome, address


def extract_fields(texts, boxes=None):
    # Baseline/legado permanece intocado.
    # O parser dedicado só entra quando o documento contém Unidade Consumidora.
    if is_digital_layout(texts):
        digital_nome, digital_endereco = parse_digital_customer(texts, boxes)
        if digital_nome and digital_endereco and address_score(digital_endereco) >= 5:
            nome, endereco = digital_nome, digital_endereco
        else:
            nome, endereco = merge_name_and_address(texts)
    else:
        nome, endereco = merge_name_and_address(texts)

    valor_topo = find_money_after_anchor(texts, "Valor a pagar", max_distance=8)
    valor_total = find_money_after_anchor(texts, "Total a Pagar", max_distance=8)

    ocr_concordante = (
        valor_topo is not None
        and valor_total is not None
        and abs(valor_topo["value"] - valor_total["value"]) < 0.001
    )

    valor = None
    if ocr_concordante:
        valor = valor_topo["value"]
    elif valor_topo:
        valor = valor_topo["value"]
    elif valor_total:
        valor = valor_total["value"]

    linha_digitavel = find_barcode_line(texts)
    febraban = validate_febraban_linha_digitavel(linha_digitavel)

    valor_codigo = febraban.get("valor") if febraban else None

    valor_confere_codigo = (
        febraban.get("valido") is True
        and valor is not None
        and valor_codigo is not None
        and abs(valor - valor_codigo) < 0.001
    )

    # Só marcamos o valor como validado quando:
    # 1. OCR do topo e do rodapé concordam;
    # 2. linha FEBRABAN é matematicamente válida;
    # 3. o código representa valor efetivo;
    # 4. o valor do código é igual ao valor do OCR.
    valor_validado = (
        ocr_concordante
        and febraban.get("valido") is True
        and febraban.get("valor_efetivo") is True
        and valor_confere_codigo
    )

    return {
        "nome": nome,
        "endereco": endereco,
        "identificador": find_identificador(texts),
        "referencia": find_reference_near_top(texts),
        "vencimento": find_date_after_anchor(texts, "Vencimento", max_distance=8),
        "valor": valor,
        "impostoRetidoIRPJ": find_imposto_retido_irpj(texts, boxes),
        "valorValidado": valor_validado,
        "linhaDigitavel": linha_digitavel,
        "validacao": {
            "ocr_topo": valor_topo["value"] if valor_topo else None,
            "ocr_rodape": valor_total["value"] if valor_total else None,
            "ocr_concordante": ocr_concordante,
            "codigo_barras": valor_codigo,
            "codigo_barras_valido": febraban.get("valido", False),
            "valor_confere_codigo_barras": valor_confere_codigo,
            "febraban": febraban
        }
    }


def prepare_ocr_image(input_path: Path):
    """
    Prepara o documento para o PaddleOCR.

    v0.21:
    - PDF: mantém exatamente a renderização estável em 200 DPI.
    - JPG/JPEG/PNG: se a maior dimensão for <= 1800 px, usa o arquivo original
      sem qualquer alteração.
    - Fotos maiores são reduzidas proporcionalmente para no máximo 1800 px,
      preservando a proporção e corrigindo orientação EXIF.

    Retorna:
      (
        caminho_da_imagem,
        caminho_temporario_ou_None,
        metadata_preparacao
      )
    """
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(str(input_path))
        if len(doc) == 0:
            doc.close()
            raise RuntimeError("PDF sem páginas.")

        page = doc[0]
        zoom = 200 / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)

        temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        temp_path = Path(temp.name)
        temp.close()
        pix.save(str(temp_path))
        doc.close()

        with Image.open(str(temp_path)) as rendered:
            processed_size = rendered.size

        return temp_path, temp_path, {
            "tipo": "pdf_200dpi",
            "redimensionada": False,
            "dimensoes_originais": None,
            "dimensoes_processadas": list(processed_size),
        }

    # Raster: abre apenas para verificar orientação e dimensões.
    with Image.open(str(input_path)) as img:
        img = ImageOps.exif_transpose(img)
        original_size = img.size
        max_dim = max(original_size)

        # Caso mais seguro: nenhuma transformação.
        if max_dim <= MAX_RASTER_DIMENSION:
            return input_path, None, {
                "tipo": "raster_original",
                "redimensionada": False,
                "dimensoes_originais": list(original_size),
                "dimensoes_processadas": list(original_size),
            }

        ratio = MAX_RASTER_DIMENSION / float(max_dim)
        new_size = (
            max(1, int(round(original_size[0] * ratio))),
            max(1, int(round(original_size[1] * ratio))),
        )

        resized = img.convert("RGB").resize(
            new_size,
            Image.Resampling.LANCZOS
        )

        # PNG temporário evita compressão JPEG adicional antes do OCR.
        temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        temp_path = Path(temp.name)
        temp.close()
        resized.save(temp_path, format="PNG", optimize=False)

    return temp_path, temp_path, {
        "tipo": "raster_redimensionado",
        "redimensionada": True,
        "dimensoes_originais": list(original_size),
        "dimensoes_processadas": list(new_size),
    }


def find_instalacao_legacy(texts):
    """
    Mantém a regra legada que já funcionou na conta de referência
    (ELAINE MARTINS DE SOUZA / instalação 3000195637).
    """
    return parse_installation(texts)


def find_identificador(texts):
    """
    Contrato neutro para suportar dois padrões CEMIG sem quebrar o legado.

    Prioridade:
    1. instalação (layout legado já validado)
    2. unidade consumidora (layout digital)
    """
    instalacao = find_instalacao_legacy(texts)
    if instalacao:
        return {
            "tipo": "instalacao",
            "valor": instalacao
        }

    unidade = find_unidade_consumidora(texts)
    if unidade:
        return {
            "tipo": "unidade_consumidora",
            "valor": unidade
        }

    return None

def find_unidade_consumidora(texts):
    anchors = ("UNIDADE CONSUMIDORA", "N.º DA UNIDADE CONSUMIDORA",
               "N° DA UNIDADE CONSUMIDORA", "Nº DA UNIDADE CONSUMIDORA")
    for i, text in enumerate(texts):
        if any(a in text.upper() for a in anchors):
            # Pode estar na mesma linha.
            m = re.search(r"\d{1,2}(?:\.\d{3}){3}-\d{2}", text)
            if m:
                return m.group(0)
            # Ou logo abaixo/ao lado na ordem OCR.
            for j in range(i + 1, min(i + 7, len(texts))):
                m = re.search(r"\d{1,2}(?:\.\d{3}){3}-\d{2}", texts[j])
                if m:
                    return m.group(0)
    return None


def box_center_x(box):
    try:
        if len(box) == 4 and not hasattr(box[0], "__len__"):
            return (float(box[0]) + float(box[2])) / 2.0
        xs = [float(pt[0]) for pt in box]
        return sum(xs) / len(xs)
    except Exception:
        return None


def extract_money_tokens(text):
    """
    Retorna todos os valores monetários reconhecíveis de uma string.
    Exemplos:
      '-1,11' -> 1.11
      '1.299,70-' -> 1299.70
    """
    results = []
    pattern = re.compile(
        r"(?:R\$)?\s*(-?\d{1,3}(?:\.\d{3})*|-?\d+),([0-9]{2})(-?)",
        re.IGNORECASE
    )

    for m in pattern.finditer(text):
        inteiro = m.group(1).replace(".", "")
        centavos = m.group(2)
        try:
            value = float(f"{inteiro}.{centavos}")

            # Mantém o sinal real da retenção.
            # Exemplos:
            #   -1,11  -> -1.11
            #   1.299,70- -> -1299.70
            if m.group(3) == "-" and value > 0:
                value = -value

            results.append(value)
        except ValueError:
            pass

    return results


def box_geometry(box):
    """
    Converte rec_box ou quadrilátero OCR em geometria útil:
    center_x, center_y, height, left_x, right_x e inclinação aproximada da linha.
    """
    try:
        # rec_box = [x1, y1, x2, y2]
        if len(box) == 4 and not hasattr(box[0], "__len__"):
            x1, y1, x2, y2 = map(float, box)
            return {
                "cx": (x1 + x2) / 2.0,
                "cy": (y1 + y2) / 2.0,
                "height": max(1.0, abs(y2 - y1)),
                "left_x": min(x1, x2),
                "right_x": max(x1, x2),
                "slope": 0.0,
            }

        pts = [(float(pt[0]), float(pt[1])) for pt in box]
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]

        # Ordena por x e estima o eixo central da caixa a partir das laterais.
        pts_sorted = sorted(pts, key=lambda pt: pt[0])
        left_pts = pts_sorted[:2]
        right_pts = pts_sorted[-2:]

        left_x = sum(pt[0] for pt in left_pts) / len(left_pts)
        left_y = sum(pt[1] for pt in left_pts) / len(left_pts)
        right_x = sum(pt[0] for pt in right_pts) / len(right_pts)
        right_y = sum(pt[1] for pt in right_pts) / len(right_pts)

        dx = right_x - left_x
        slope = (right_y - left_y) / dx if abs(dx) > 1e-6 else 0.0

        # Altura aproximada pela extensão vertical média.
        height = max(1.0, max(ys) - min(ys))

        return {
            "cx": sum(xs) / len(xs),
            "cy": sum(ys) / len(ys),
            "height": height,
            "left_x": min(xs),
            "right_x": max(xs),
            "slope": slope,
        }
    except Exception:
        return None


def projected_row_y(anchor_geom, x):
    """
    Projeta o eixo central da linha do rótulo até a coordenada x.
    Isso compensa inclinação/perspectiva da foto.
    """
    return anchor_geom["cy"] + anchor_geom["slope"] * (x - anchor_geom["cx"])


def find_imposto_retido_irpj(texts, boxes):
    """
    Extrai especificamente 'Imposto Retido - IRPJ' reconstruindo a linha visual.

    Estratégia v0.17:
    1. localiza o rótulo IRPJ;
    2. usa o quadrilátero do OCR para estimar a inclinação da linha;
    3. projeta essa linha até cada candidato monetário;
    4. mede o erro vertical do candidato em relação à linha projetada;
    5. mantém apenas candidatos realmente pertencentes à mesma linha;
    6. entre eles, seleciona a primeira coluna monetária à direita;
    7. preserva o sinal real da retenção.

    Se a associação espacial não for confiável, retorna None.
    """
    anchor_indices = [
        i for i, t in enumerate(texts)
        if "IRPJ" in t.upper() and "IMPOST" in t.upper()
    ]

    if not anchor_indices or boxes is None:
        return None

    for anchor_idx in anchor_indices:
        anchor_text = texts[anchor_idx]

        # Caso raro: rótulo e valor vieram juntos no mesmo fragmento.
        values_same_box = extract_money_tokens(anchor_text)
        if values_same_box:
            return values_same_box[-1]

        if anchor_idx >= len(boxes):
            continue

        anchor_geom = box_geometry(boxes[anchor_idx])
        if not anchor_geom:
            continue

        candidates = []

        for i, text in enumerate(texts):
            if i == anchor_idx or i >= len(boxes):
                continue

            values = extract_money_tokens(text)
            if not values:
                continue

            geom = box_geometry(boxes[i])
            if not geom:
                continue

            # Só valores localizados à direita do rótulo.
            if geom["cx"] <= anchor_geom["cx"]:
                continue

            expected_y = projected_row_y(anchor_geom, geom["cx"])
            row_error = abs(geom["cy"] - expected_y)

            # Normaliza pela altura do texto. Mantém faixa estreita o bastante
            # para rejeitar a linha de cima/baixo, mesmo em fotos inclinadas.
            reference_h = max(anchor_geom["height"], geom["height"], 1.0)
            normalized_error = row_error / reference_h

            # Exige forte compatibilidade de linha.
            if normalized_error <= 0.75:
                for value in values:
                    candidates.append({
                        "value": value,
                        "row_error": row_error,
                        "normalized_error": normalized_error,
                        "dx": geom["cx"] - anchor_geom["right_x"],
                        "text": text,
                    })

        print("\n--- DIAGNÓSTICO IRPJ v0.17 ---")
        print(
            f'Âncora="{anchor_text}" '
            f'cx={anchor_geom["cx"]:.2f} cy={anchor_geom["cy"]:.2f} '
            f'slope={anchor_geom["slope"]:.5f} h={anchor_geom["height"]:.2f}'
        )

        for c in sorted(candidates, key=lambda c: (c["normalized_error"], c["dx"])):
            print(
                f'candidato texto="{c["text"]}" valor={c["value"]} '
                f'erro_linha={c["row_error"]:.2f} '
                f'erro_norm={c["normalized_error"]:.3f} '
                f'dx={c["dx"]:.2f}'
            )

        if not candidates:
            print("IRPJ: nenhum candidato confiável na mesma linha.")
            print("--- FIM DIAGNÓSTICO IRPJ v0.17 ---\n")
            continue

        # Primeiro restringe à melhor faixa de alinhamento encontrada.
        best_norm = min(c["normalized_error"] for c in candidates)
        row_band = [
            c for c in candidates
            if c["normalized_error"] <= best_norm + 0.20
        ]

        # Dentro da mesma linha, o valor do item é a primeira coluna monetária à direita.
        row_band.sort(key=lambda c: (c["dx"], c["normalized_error"]))
        chosen = row_band[0]

        print(
            f'IRPJ escolhido: texto="{chosen["text"]}" '
            f'valor={chosen["value"]} '
            f'erro_norm={chosen["normalized_error"]:.3f} '
            f'dx={chosen["dx"]:.2f}'
        )
        print("--- FIM DIAGNÓSTICO IRPJ v0.17 ---\n")
        return chosen["value"]

    return None

def has_irpj_anchor(texts):
    return any(
        "IRPJ" in t.upper() and "IMPOST" in t.upper()
        for t in texts
    )


def find_irpj_anchor_index(texts):
    return next(
        (
            i for i, t in enumerate(texts)
            if "IRPJ" in t.upper() and "IMPOST" in t.upper()
        ),
        None
    )


def extract_money_tokens_flexible(text):
    """
    Parser monetário específico para o fallback do IRPJ.

    Aceita decimal com vírgula ou ponto:
      -1,11
      -1.11
      52,70
      52.70

    Também aceita sinal negativo ao final:
      624,31-
      624.31-

    Retorna lista de dicts preservando se o sinal negativo veio
    explicitamente do OCR.
    """
    results = []

    pattern = re.compile(
        r"(?:R\$)?\s*"
        r"(?P<lead>-)?"
        r"(?P<int>\d{1,3}(?:\.\d{3})*|\d+)"
        r"(?P<sep>[,.])"
        r"(?P<dec>\d{2})"
        r"(?P<trail>-)?",
        re.IGNORECASE
    )

    for m in pattern.finditer(text):
        inteiro_raw = m.group("int")
        sep = m.group("sep")
        dec = m.group("dec")
        negative = bool(m.group("lead") or m.group("trail"))

        # Se o separador decimal for vírgula, pontos anteriores são milhares.
        # Se o separador decimal for ponto, remove apenas milhares por vírgula
        # (não esperado aqui, mas evita ambiguidade).
        if sep == ",":
            inteiro = inteiro_raw.replace(".", "")
        else:
            inteiro = inteiro_raw.replace(",", "")

        try:
            value = float(f"{inteiro}.{dec}")
        except ValueError:
            continue

        if negative:
            value = -abs(value)

        results.append({
            "value": value,
            "explicit_negative": negative,
            "raw": m.group(0).strip()
        })

    return results

def fallback_ocr_irpj_local(ocr, image_path, texts, boxes):
    """
    Fallback da v0.18.

    Só é chamado quando:
    - existe o rótulo 'Imposto Retido - IRPJ'; e
    - a leitura espacial principal não encontrou uma retenção negativa confiável.

    Estratégia:
    1. localiza a caixa do rótulo IRPJ;
    2. recorta uma faixa MUITO estreita da mesma linha, somente à direita do rótulo;
    3. amplia 4x;
    4. aplica escala de cinza + autocontraste + nitidez;
    5. roda o mesmo PaddleOCR apenas nesse pequeno recorte;
    6. seleciona o primeiro valor monetário não-zero da esquerda para a direita;
    7. força sinal negativo porque o campo é explicitamente uma retenção.

    Se não houver leitura confiável, retorna None.
    """
    anchor_idx = find_irpj_anchor_index(texts)
    if anchor_idx is None or boxes is None or anchor_idx >= len(boxes):
        return None

    anchor_geom = box_geometry(boxes[anchor_idx])
    if not anchor_geom:
        return None

    with Image.open(str(image_path)) as img:
        img = img.convert("RGB")
        width, height = img.size

        # Faixa vertical estreita para evitar capturar a linha acima/abaixo.
        half_band = max(8, int(anchor_geom["height"] * 0.60))

        left = max(0, int(anchor_geom["right_x"] + 3))
        top = max(0, int(anchor_geom["cy"] - half_band))
        right = width
        bottom = min(height, int(anchor_geom["cy"] + half_band))

        if right <= left or bottom <= top:
            return None

        crop = img.crop((left, top, right, bottom))

        # Aumenta bastante a região pequena para melhorar caracteres como "-1,11".
        scale = 4
        crop = crop.resize(
            (crop.width * scale, crop.height * scale),
            Image.Resampling.LANCZOS
        )

        gray = ImageOps.grayscale(crop)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(1.7)
        gray = gray.filter(ImageFilter.SHARPEN)
        prepared = gray.convert("RGB")

        debug_crop = OUTPUT_DIR / "irpj_crop_debug.png"
        prepared.save(debug_crop)

    temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    prepared.save(temp_path)

    try:
        pages = list(ocr.predict(str(temp_path)))
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not pages:
        print("\n--- FALLBACK OCR IRPJ v0.18 ---")
        print("Nenhum texto reconhecido no recorte.")
        print("--- FIM FALLBACK OCR IRPJ v0.18 ---\n")
        return None

    page = pages[0]
    data = page.json["res"] if hasattr(page, "json") else page["res"]
    crop_texts = [normalize_text(t) for t in data.get("rec_texts", [])]
    crop_boxes = data.get("dt_polys", data.get("rec_boxes", []))

    candidates = []

    for i, text in enumerate(crop_texts):
        values = extract_money_tokens_flexible(text)
        if not values:
            continue

        x = None
        if i < len(crop_boxes):
            geom = box_geometry(crop_boxes[i])
            if geom:
                x = geom["left_x"]

        for item in values:
            value = item["value"]

            if abs(value) < 0.0001:
                continue

            candidates.append({
                "text": text,
                "raw": item["raw"],
                "value": value,
                "explicit_negative": item["explicit_negative"],
                "x": x if x is not None else 999999.0
            })

    print("\n--- FALLBACK OCR IRPJ v0.18 ---")
    print(f"Recorte salvo em: {debug_crop}")
    print("OCR do recorte:", crop_texts)

    if not candidates:
        print("Nenhum valor monetário não-zero encontrado no recorte.")
        print("--- FIM FALLBACK OCR IRPJ v0.18 ---\n")
        return None

    negative_candidates = [
        c for c in candidates
        if c["explicit_negative"] or c["value"] < 0
    ]

    if negative_candidates:
        # Para "Imposto Retido - IRPJ", um valor explicitamente negativo
        # reconhecido pelo OCR é a evidência mais forte.
        negative_candidates.sort(key=lambda c: c["x"])
        chosen = negative_candidates[0]
        result = chosen["value"]
        criterio = "negativo explícito"
    else:
        # Fallback secundário: primeiro valor monetário não-zero da esquerda
        # para a direita, convertido para retenção negativa.
        candidates.sort(key=lambda c: c["x"])
        chosen = candidates[0]
        result = -abs(chosen["value"])
        criterio = "posição (sem negativo explícito)"

    print(
        f'IRPJ fallback escolhido: texto="{chosen["text"]}" '
        f'raw="{chosen.get("raw")}" '
        f'valor_original={chosen["value"]} '
        f'critério="{criterio}" resultado={result}'
    )
    print("--- FIM FALLBACK OCR IRPJ v0.18 ---\n")

    return result

def find_tax_value(texts, tax_name):
    """
    Procura explicitamente o rótulo do tributo.
    Se não existir no documento, retorna None.
    Mantém a estratégia textual desta versão; associação espacial virá depois
    se os testes mostrarem necessidade neste layout focado.
    """
    target = tax_name.upper()
    for i, text in enumerate(texts):
        if target not in text.upper():
            continue

        parsed = parse_money(text)
        if parsed:
            return abs(parsed["value"])

        for j in range(i + 1, min(i + 4, len(texts))):
            # Não atravessa para outro rótulo tributário conhecido.
            upper = texts[j].upper()
            if ("IRPJ" in upper or "IRPF" in upper) and target not in upper:
                break
            parsed = parse_money(texts[j])
            if parsed:
                return abs(parsed["value"])
    return None

def create_ocr():
    """Motor robusto/baseline: small_det + small_rec."""
    return PaddleOCR(
        lang="pt",
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name="PP-OCRv6_small_det",
        text_recognition_model_name="PP-OCRv6_small_rec",
    )


def create_ocr_fast():
    """Motor rápido: tiny_det + small_rec."""
    return PaddleOCR(
        lang="pt",
        enable_mkldnn=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name="PP-OCRv6_tiny_det",
        text_recognition_model_name="PP-OCRv6_small_rec",
    )


def _get_document_dimensions_for_router(input_path: Path):
    """
    Obtém somente as dimensões da página/imagem, sem OCR e sem renderização
    de alta resolução.

    Retorna largura, altura e origem da medição.
    """
    input_path = Path(input_path)
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        doc = fitz.open(str(input_path))
        if len(doc) == 0:
            doc.close()
            raise RuntimeError("PDF sem páginas.")
        page = doc[0]
        rect = page.rect
        width = float(rect.width)
        height = float(rect.height)
        doc.close()
        return width, height, "pdf_page_points"

    with Image.open(str(input_path)) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size

    return float(width), float(height), "raster_dimensions"


def classify_layout_visual(input_path: Path):
    """
    Router visual v0.41: SEM OCR.

    Hipótese experimental baseada no conjunto atual de regressão:
      - layouts Elaine/Jurandir têm proporção largura/altura ~0.707-0.715
      - layouts Município/Fiscalização têm proporção ~0.750

    Regra conservadora:
      <= 0.725  -> motor rápido
      >= 0.740  -> motor robusto
      zona intermediária -> robusto

    Isso NÃO tenta identificar cliente, texto ou conteúdo. É um experimento
    de custo mínimo para validar se uma característica geométrica simples
    consegue separar as famílias atuais. Se houver dúvida, escolhe robusto.
    """
    start = time.perf_counter()
    width, height, source = _get_document_dimensions_for_router(input_path)

    if height <= 0:
        raise RuntimeError("Altura inválida para classificação visual.")

    # Normaliza orientação: documentos são esperados em retrato.
    short_side = min(width, height)
    long_side = max(width, height)
    aspect_ratio = short_side / long_side

    FAST_MAX = 0.725
    ROBUST_MIN = 0.740

    if aspect_ratio <= FAST_MAX:
        layout = "familia_visual_estreita"
        motor = "rapido"
        motivo = "aspect_ratio_compatível_com_layouts_rapidos"
        confidence = min(1.0, max(0.0, (FAST_MAX - aspect_ratio) / 0.025 + 0.55))
    elif aspect_ratio >= ROBUST_MIN:
        layout = "familia_visual_larga"
        motor = "robusto"
        motivo = "aspect_ratio_compatível_com_layouts_robustos"
        confidence = min(1.0, max(0.0, (aspect_ratio - ROBUST_MIN) / 0.025 + 0.55))
    else:
        layout = "familia_visual_ambigua"
        motor = "robusto"
        motivo = "zona_de_incerteza_visual_fallback_conservador"
        confidence = 0.0

    return {
        "layout": layout,
        "motor": motor,
        "motivo": motivo,
        "confianca_heuristica": round(confidence, 4),
        "aspect_ratio": round(aspect_ratio, 6),
        "limites": {
            "rapido_ate": FAST_MAX,
            "robusto_a_partir": ROBUST_MIN,
        },
        "dimensoes_router": [round(width, 2), round(height, 2)],
        "fonte_dimensoes": source,
        "tempo_total_s": round(time.perf_counter() - start, 6),
    }



def process_document(input_path: Path, ocr, save_debug: bool = False):
    """
    Executa exatamente o pipeline OCR/parser validado na v0.19 sobre um arquivo.

    Parâmetros:
      input_path: JPG/JPEG/PNG/PDF.
      ocr: instância PaddleOCR já carregada (singleton na API).
      save_debug: quando True, salva OCR bruto e JSON em output/.

    Retorna:
      (resultado_json, tempos)
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {input_path}")

    if input_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".pdf"}:
        raise ValueError("Formato não suportado. Use JPG, JPEG, PNG ou PDF.")

    total_start = time.perf_counter()
    temp_ocr_path = None

    try:
        t0 = time.perf_counter()
        ocr_image_path, temp_ocr_path, prep_metadata = prepare_ocr_image(input_path)
        preparation_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        pages = list(ocr.predict(str(ocr_image_path)))
        inference_time = time.perf_counter() - t0

        if not pages:
            raise RuntimeError("Nenhum resultado retornado pelo PaddleOCR.")

        page = pages[0]
        data = page.json["res"] if hasattr(page, "json") else page["res"]
        texts = [normalize_text(t) for t in data["rec_texts"]]
        boxes = data.get("dt_polys", data.get("rec_boxes", []))

        t0 = time.perf_counter()
        extracted = extract_fields(texts, boxes)

        # Mantém exatamente o fallback local aprovado na v0.19.
        if has_irpj_anchor(texts):
            irpj_atual = extracted.get("impostoRetidoIRPJ")
            if irpj_atual is None or irpj_atual >= 0:
                irpj_fallback = fallback_ocr_irpj_local(
                    ocr, ocr_image_path, texts, boxes
                )
                if irpj_fallback is not None:
                    extracted["impostoRetidoIRPJ"] = irpj_fallback
                else:
                    extracted["impostoRetidoIRPJ"] = None

        parser_time = time.perf_counter() - t0
        total_time = time.perf_counter() - total_start

        timings = {
            "preparacao_imagem_s": round(preparation_time, 4),
            "inferencia_ocr_s": round(inference_time, 4),
            "parser_febraban_s": round(parser_time, 4),
            "total_s": round(total_time, 4),
            "imagem": prep_metadata,
        }

        if save_debug:
            safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", input_path.stem)
            (OUTPUT_DIR / f"{safe_stem}_ocr.txt").write_text(
                "\n".join(f"{i:03d} {t}" for i, t in enumerate(texts, start=1)),
                encoding="utf-8"
            )
            (OUTPUT_DIR / f"{safe_stem}_resultado.json").write_text(
                json.dumps(extracted, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        return extracted, timings

    finally:
        if temp_ocr_path is not None:
            try:
                temp_ocr_path.unlink(missing_ok=True)
            except Exception:
                pass
