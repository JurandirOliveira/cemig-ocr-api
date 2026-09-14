import gc
import importlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="CEMIG OCR API - Survey123 RC13.1",
    version="1.0.0-RC13.1.1",
    description="CEMIG OCR API RC13.1: OCR CEMIG SIM / GD no webhook Survey123.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

OCR_FAST = None
OCR_ROBUSTO = None


def ambiente():
    return {
        "plataforma": "vercel" if os.environ.get("VERCEL") else "local",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "tmpdir": tempfile.gettempdir(),
        "paddle_cache": os.environ.get("PADDLE_PDX_CACHE_HOME"),
        "cwd": str(Path.cwd()),
    }


def erro_payload(etapa: str, exc: Exception, inicio: float):
    return {
        "ok": False,
        "etapa": etapa,
        "erro_tipo": type(exc).__name__,
        "erro": str(exc),
        "tempo_s": round(time.perf_counter() - inicio, 4),
        "traceback": traceback.format_exc(limit=12),
        "ambiente": ambiente(),
    }


@app.get("/")
def raiz():
    return {
        "status": "ok",
        "versao": "1.0.0-RC13.1.1",
        "mensagem": "FastAPI iniciou sem carregar Paddle/PaddleOCR. RC13.1 com OCR CEMIG SIM / GD.",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "versao": "1.0.0-RC13.1.1",
        "ocr_fast_carregado": OCR_FAST is not None,
        "ocr_robusto_carregado": OCR_ROBUSTO is not None,
        "ambiente": ambiente(),
    }



MAX_UPLOAD_BYTES = 4 * 1024 * 1024
FORMATOS_SUPORTADOS = {".jpg", ".jpeg", ".png", ".pdf"}


def _obter_motor_para_documento(caminho: Path):
    """Roteia visualmente e carrega somente o motor necessário para esta requisição."""
    global OCR_FAST, OCR_ROBUSTO
    from ocr_engine import classify_layout_visual, create_ocr, create_ocr_fast

    roteamento = classify_layout_visual(caminho)
    inicio = time.perf_counter()

    if roteamento["motor"] == "rapido":
        if OCR_FAST is None:
            OCR_FAST = create_ocr_fast()
        motor = OCR_FAST
    else:
        if OCR_ROBUSTO is None:
            OCR_ROBUSTO = create_ocr()
        motor = OCR_ROBUSTO

    roteamento["tempo_carregamento_motor_s"] = round(time.perf_counter() - inicio, 4)
    roteamento["motor_escolhido"] = roteamento["motor"]
    return motor, roteamento


@app.post("/ocr/conta-cemig")
async def ocr_conta_cemig(arquivo: UploadFile = File(...)):
    """Executa o pipeline real v0.41 na Vercel, com router visual antes do OCR."""
    inicio_total = time.perf_counter()
    nome = arquivo.filename or "arquivo"
    sufixo = Path(nome).suffix.lower()

    if sufixo not in FORMATOS_SUPORTADOS:
        raise HTTPException(status_code=415, detail="Formato não suportado. Use JPG, JPEG, PNG ou PDF.")

    conteudo = await arquivo.read(MAX_UPLOAD_BYTES + 1)
    if len(conteudo) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Arquivo excede o limite de 4 MB desta API.")
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    fd, nome_tmp = tempfile.mkstemp(prefix="cemig_", suffix=sufixo, dir="/tmp")
    os.close(fd)
    caminho = Path(nome_tmp)

    try:
        caminho.write_bytes(conteudo)
        motor, roteamento = _obter_motor_para_documento(caminho)
        from ocr_engine import process_document
        resultado, tempos = process_document(caminho, motor, save_debug=False)

        return {
            "sucesso": True,
            "versao": "1.0.0-RC13.1.1",
            "arquivo": nome,
            "roteamento": roteamento,
            "resultado": resultado,
            "tempos": {
                **tempos,
                "pipeline_completo_s": round(time.perf_counter() - inicio_total, 4),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[OCR REAL] ERRO: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(
            status_code=500,
            detail={
                "erro_tipo": type(exc).__name__,
                "erro": str(exc),
                "traceback": traceback.format_exc(limit=12),
            },
        )
    finally:
        try:
            caminho.unlink(missing_ok=True)
        except Exception:
            pass



@app.post("/ocr/fatura-cemig-sim")
async def ocr_fatura_cemig_sim(arquivo: UploadFile = File(...)):
    """Executa o parser dedicado da fatura CEMIG SIM usada em contas GD."""
    inicio_total = time.perf_counter()
    nome = arquivo.filename or "arquivo"
    sufixo = Path(nome).suffix.lower()

    if sufixo not in FORMATOS_SUPORTADOS:
        raise HTTPException(status_code=415, detail="Formato não suportado. Use JPG, JPEG, PNG ou PDF.")

    conteudo = await arquivo.read(MAX_UPLOAD_BYTES + 1)
    if len(conteudo) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Arquivo excede o limite de 4 MB desta API.")
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    fd, nome_tmp = tempfile.mkstemp(prefix="cemig_sim_", suffix=sufixo, dir="/tmp")
    os.close(fd)
    caminho = Path(nome_tmp)

    try:
        caminho.write_bytes(conteudo)
        from ocr_engine import process_document_sim

        roteamento = {
            "layout": "cemig_sim",
            "motor": "pdf_text_direto_ou_auto",
            "motor_escolhido": None,
        }
        try:
            resultado, tempos = process_document_sim(caminho, ocr=None, save_debug=False)
        except RuntimeError:
            motor, roteamento = _obter_motor_para_documento(caminho)
            resultado, tempos = process_document_sim(caminho, ocr=motor, save_debug=False)

        return {
            "sucesso": True,
            "versao": "1.0.0-RC13.1.1",
            "arquivo": nome,
            "roteamento": roteamento,
            "resultado": resultado,
            "tempos": {
                **tempos,
                "pipeline_completo_s": round(time.perf_counter() - inicio_total, 4),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[OCR SIM] ERRO: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(
            status_code=500,
            detail={
                "erro_tipo": type(exc).__name__,
                "erro": str(exc),
                "traceback": traceback.format_exc(limit=12),
            },
        )
    finally:
        try:
            caminho.unlink(missing_ok=True)
        except Exception:
            pass

@app.get("/diagnostico/00-import-cv2")
def diagnostico_import_cv2():
    inicio = time.perf_counter()
    print("[DIAG 00] Antes de import cv2", flush=True)
    try:
        import cv2
        print("[DIAG 00] import cv2 OK", flush=True)
        return {
            "ok": True,
            "etapa": "import_cv2",
            "cv2_version": getattr(cv2, "__version__", None),
            "cv2_arquivo": getattr(cv2, "__file__", None),
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 00] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("import_cv2", exc, inicio)


@app.get("/diagnostico/01-import-paddle")
def diagnostico_import_paddle():
    inicio = time.perf_counter()
    print("[DIAG 01] Antes de import paddle", flush=True)
    try:
        import paddle
        print("[DIAG 01] import paddle OK", flush=True)
        return {
            "ok": True,
            "etapa": "import_paddle",
            "paddle_version": getattr(paddle, "__version__", None),
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 01] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("import_paddle", exc, inicio)


@app.get("/diagnostico/02-import-paddleocr")
def diagnostico_import_paddleocr():
    inicio = time.perf_counter()
    print("[DIAG 02] Antes de import paddleocr", flush=True)
    try:
        import paddleocr
        from paddleocr import PaddleOCR
        print("[DIAG 02] import paddleocr OK", flush=True)
        return {
            "ok": True,
            "etapa": "import_paddleocr",
            "paddleocr_version": getattr(paddleocr, "__version__", None),
            "classe": str(PaddleOCR),
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 02] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("import_paddleocr", exc, inicio)


@app.get("/diagnostico/03-import-ocr-engine")
def diagnostico_import_ocr_engine():
    inicio = time.perf_counter()
    print("[DIAG 03] Antes de import ocr_engine", flush=True)
    try:
        modulo = importlib.import_module("ocr_engine")
        print("[DIAG 03] import ocr_engine OK", flush=True)
        return {
            "ok": True,
            "etapa": "import_ocr_engine",
            "arquivo": getattr(modulo, "__file__", None),
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 03] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("import_ocr_engine", exc, inicio)


@app.get("/diagnostico/04-carregar-rapido")
def diagnostico_carregar_rapido():
    global OCR_FAST
    inicio = time.perf_counter()
    print("[DIAG 04] Antes de carregar motor rápido", flush=True)
    try:
        from ocr_engine import create_ocr_fast
        if OCR_FAST is None:
            OCR_FAST = create_ocr_fast()
        print("[DIAG 04] motor rápido OK", flush=True)
        return {
            "ok": True,
            "etapa": "carregar_rapido",
            "carregado": OCR_FAST is not None,
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 04] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("carregar_rapido", exc, inicio)


@app.get("/diagnostico/05-carregar-robusto")
def diagnostico_carregar_robusto():
    global OCR_ROBUSTO
    inicio = time.perf_counter()
    print("[DIAG 05] Antes de carregar motor robusto", flush=True)
    try:
        from ocr_engine import create_ocr
        if OCR_ROBUSTO is None:
            OCR_ROBUSTO = create_ocr()
        print("[DIAG 05] motor robusto OK", flush=True)
        return {
            "ok": True,
            "etapa": "carregar_robusto",
            "carregado": OCR_ROBUSTO is not None,
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 05] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("carregar_robusto", exc, inicio)


@app.get("/diagnostico/06-carregar-dois")
def diagnostico_carregar_dois():
    global OCR_FAST, OCR_ROBUSTO
    inicio = time.perf_counter()
    print("[DIAG 06] Antes de carregar os dois motores", flush=True)
    try:
        from ocr_engine import create_ocr, create_ocr_fast
        if OCR_FAST is None:
            OCR_FAST = create_ocr_fast()
        if OCR_ROBUSTO is None:
            OCR_ROBUSTO = create_ocr()
        print("[DIAG 06] dois motores OK", flush=True)
        return {
            "ok": True,
            "etapa": "carregar_dois",
            "ocr_fast": OCR_FAST is not None,
            "ocr_robusto": OCR_ROBUSTO is not None,
            "tempo_s": round(time.perf_counter() - inicio, 4),
            "ambiente": ambiente(),
        }
    except Exception as exc:
        print(f"[DIAG 06] ERRO: {type(exc).__name__}: {exc}", flush=True)
        return erro_payload("carregar_dois", exc, inicio)


@app.get("/diagnostico/07-liberar-modelos")
def diagnostico_liberar_modelos():
    global OCR_FAST, OCR_ROBUSTO
    OCR_FAST = None
    OCR_ROBUSTO = None
    gc.collect()
    return {
        "ok": True,
        "etapa": "liberar_modelos",
        "ocr_fast": False,
        "ocr_robusto": False,
    }



def _json_preview(valor, limite=600):
    try:
        texto = json.dumps(valor, ensure_ascii=False, default=str)
    except Exception:
        texto = str(valor)
    if len(texto) > limite:
        return texto[:limite] + "... [truncado]"
    return texto


def _tipo_resumido(valor):
    """Resumo curto de tipo/tamanho para diagnosticar payload sem vazar conteúdo sensível."""
    if isinstance(valor, dict):
        return {"tipo": "dict", "keys": list(valor.keys())[:30], "len": len(valor)}
    if isinstance(valor, list):
        return {"tipo": "list", "len": len(valor), "item_types": [type(i).__name__ for i in valor[:10]]}
    if isinstance(valor, str):
        return {"tipo": "str", "len": len(valor), "preview": valor[:120]}
    return {"tipo": type(valor).__name__, "valor": valor}


def _buscar_por_termos_em_chaves(obj, termos, caminho="$", encontrados=None, limite=80):
    """Localiza caminhos cujas chaves contenham termos importantes, com previews mascarados."""
    if encontrados is None:
        encontrados = []
    if len(encontrados) >= limite:
        return encontrados

    termos_norm = [str(t).lower() for t in termos]

    if isinstance(obj, dict):
        for chave, valor in obj.items():
            if len(encontrados) >= limite:
                break
            chave_txt = str(chave)
            novo_caminho = f"{caminho}.{chave_txt}"
            chave_norm = chave_txt.lower()
            if any(t in chave_norm for t in termos_norm):
                encontrados.append(
                    {
                        "path": novo_caminho,
                        "key": chave_txt,
                        "resumo": _tipo_resumido(_mascarar_tokens(valor)),
                        "preview": _json_preview(_mascarar_tokens(valor), 1200),
                    }
                )
            _buscar_por_termos_em_chaves(valor, termos_norm, novo_caminho, encontrados, limite)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if len(encontrados) >= limite:
                break
            _buscar_por_termos_em_chaves(item, termos_norm, f"{caminho}[{i}]", encontrados, limite)

    return encontrados


def _diagnostico_detalhado_anexos(payload: dict, info: dict):
    """Monta diagnóstico focado para descobrir como o Survey123 envia múltiplos anexos.

    Não imprime token, headers nem body completo. O foco é: chaves, tipos, caminhos e
    previews pequenos das estruturas de anexos/campos do formulário.
    """
    feature = payload.get("feature") or {}
    attrs = feature.get("attributes") or {}
    result = feature.get("result") or {}
    layer_info = feature.get("layerInfo") or {}
    raw_attachments = feature.get("attachments")
    anexos = info.get("attachments") or []

    # Campos do formulário úteis para confirmar nomes reais publicados no XLSForm.
    campos_formulario = {}
    if isinstance(attrs, dict):
        for chave, valor in attrs.items():
            chave_norm = str(chave).lower()
            if any(t in chave_norm for t in ["gd", "sim", "conta", "fatura", "referencia", "unidade"]):
                campos_formulario[chave] = valor

    detalhe_anexos_extraidos = []
    for i, anexo in enumerate(anexos):
        if isinstance(anexo, dict):
            detalhe_anexos_extraidos.append(
                {
                    "i": i,
                    "keys": list(anexo.keys()),
                    "campo": anexo.get("campo"),
                    "name": anexo.get("name") or anexo.get("fileName") or anexo.get("filename"),
                    "keywords": anexo.get("keywords"),
                    "parentGlobalId": anexo.get("parentGlobalId"),
                    "parentObjectId": anexo.get("parentObjectId"),
                    "globalId": anexo.get("globalId") or anexo.get("globalid"),
                    "id": anexo.get("id") or anexo.get("attachmentId") or anexo.get("objectId"),
                    "contentType": anexo.get("contentType") or anexo.get("content_type"),
                    "size": anexo.get("size"),
                    "url_presente": bool(anexo.get("url")),
                    "url_path": urllib.parse.urlsplit(str(anexo.get("url") or "")).path if anexo.get("url") else None,
                    "preview": _json_preview(_mascarar_tokens(anexo), 1500),
                }
            )
        else:
            detalhe_anexos_extraidos.append({"i": i, "tipo": type(anexo).__name__, "preview": str(anexo)[:500]})

    return {
        "root_keys": list(payload.keys())[:40] if isinstance(payload, dict) else [],
        "feature_keys": list(feature.keys())[:40] if isinstance(feature, dict) else [],
        "feature_result_keys": list(result.keys())[:40] if isinstance(result, dict) else [],
        "feature_layerInfo_keys": list(layer_info.keys())[:40] if isinstance(layer_info, dict) else [],
        "attributes_keys_total": len(attrs) if isinstance(attrs, dict) else None,
        "attributes_keys_amostra": list(attrs.keys())[:80] if isinstance(attrs, dict) else [],
        "campos_formulario_relevantes": campos_formulario,
        "feature_attachments_resumo": _tipo_resumido(_mascarar_tokens(raw_attachments)),
        "feature_attachments_preview": _json_preview(_mascarar_tokens(raw_attachments), 3500),
        "anexos_extraidos_detalhe": detalhe_anexos_extraidos,
        "caminhos_relevantes": _buscar_por_termos_em_chaves(
            payload,
            ["attachment", "attachments", "conta", "fatura", "sim", "gd", "file", "name", "url"],
            limite=60,
        ),
    }


def _logar_diagnostico_detalhado_anexos(prefixo: str, object_id, payload: dict, info: dict):
    try:
        diag = _diagnostico_detalhado_anexos(payload, info)
        print(
            f"{prefixo} diagnostico_detalhado objectId={object_id} "
            f"estrutura={json.dumps(diag, ensure_ascii=False, default=str)}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"{prefixo} diagnostico_detalhado_erro objectId={object_id} "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )


def _mascarar_tokens(obj):
    """Remove tokens/sigilos antes de imprimir logs."""
    if isinstance(obj, dict):
        novo = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if "token" in kl or "signature" in kl or kl in {"authorization", "cookie"}:
                novo[k] = "***REMOVIDO***"
            else:
                novo[k] = _mascarar_tokens(v)
        return novo
    if isinstance(obj, list):
        return [_mascarar_tokens(i) for i in obj]
    return obj


def _buscar_chaves(obj, chaves_alvo, caminho="$", encontrados=None):
    if encontrados is None:
        encontrados = []
    chaves_norm = {c.lower() for c in chaves_alvo}

    if isinstance(obj, dict):
        for chave, valor in obj.items():
            novo_caminho = f"{caminho}.{chave}"
            if str(chave).lower() in chaves_norm:
                encontrados.append({"path": novo_caminho, "key": chave, "value": valor})
            _buscar_chaves(valor, chaves_alvo, novo_caminho, encontrados)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _buscar_chaves(item, chaves_alvo, f"{caminho}[{i}]", encontrados)

    return encontrados


def _primeiro_valor(encontrados):
    return encontrados[0]["value"] if encontrados else None


def _diagnosticar_payload_survey123(payload):
    buscas = {
        "objectId": ["objectId", "objectid", "OBJECTID", "objectID"],
        "globalId": ["globalId", "globalid", "GLOBALID", "globalID"],
        "featureServiceUrl": ["featureServiceUrl", "featureServiceURL", "featureserviceurl", "serviceUrl", "serviceURL", "layerUrl", "layerURL"],
        "layerId": ["layerId", "layerid", "layerID", "id"],
        "attachments": ["attachments", "attachment", "attachmentInfos", "attachmentInfo"],
    }

    diagnostico = {}
    detalhes = {}
    for nome, chaves in buscas.items():
        encontrados = _buscar_chaves(payload, chaves)
        detalhes[nome] = [
            {
                "path": item["path"],
                "key": item["key"],
                "value_preview": _json_preview(item["value"], 300),
            }
            for item in encontrados[:20]
        ]
        diagnostico[nome] = _primeiro_valor(encontrados)
    return diagnostico, detalhes


def _extrair_info_survey123(payload: dict):
    feature = payload.get("feature") or {}
    feature_attrs = feature.get("attributes") or {}
    feature_result = feature.get("result") or {}
    layer_info = feature.get("layerInfo") or {}
    survey_info = payload.get("surveyInfo") or {}
    portal_info = payload.get("portalInfo") or {}

    object_id = (
        feature_attrs.get("objectid")
        or feature_attrs.get("OBJECTID")
        or feature_result.get("objectId")
        or feature_result.get("objectid")
    )

    if object_id is None:
        try:
            object_id = (payload.get("response") or [{}])[0].get("addResults", [{}])[0].get("objectId")
        except Exception:
            object_id = None

    global_id = (
        feature_attrs.get("globalid")
        or feature_attrs.get("globalId")
        or feature_result.get("globalId")
        or feature_result.get("globalid")
    )

    service_url = survey_info.get("serviceUrl") or survey_info.get("featureServiceUrl")
    layer_id = layer_info.get("id")
    if layer_id is None:
        layer_id = 0

    anexos = []
    feature_attachments = feature.get("attachments") or {}
    if isinstance(feature_attachments, dict):
        for campo, lista in feature_attachments.items():
            if isinstance(lista, list):
                for item in lista:
                    if isinstance(item, dict):
                        anexos.append({"campo": campo, **item})

    if not anexos:
        try:
            adds = (payload.get("applyEdits") or [{}])[0].get("attachments", {}).get("adds", [])
            for item in adds:
                if isinstance(item, dict):
                    anexos.append({"campo": item.get("keywords") or "attachment", **item})
        except Exception:
            pass

    return {
        "objectId": object_id,
        "globalId": global_id,
        "featureServiceUrl": service_url,
        "layerId": layer_id,
        "portalToken": portal_info.get("token"),
        "attachments": anexos,
    }




def _obter_atributos_survey123(payload: dict):
    """Retorna os atributos do registro enviado pelo Survey123."""
    try:
        attrs = ((payload.get("feature") or {}).get("attributes") or {})
        return attrs if isinstance(attrs, dict) else {}
    except Exception:
        return {}


def _ler_campo_formulario(payload: dict, *nomes):
    """Lê um campo do formulário ignorando diferença de maiúsculas/minúsculas."""
    attrs = _obter_atributos_survey123(payload)
    if not attrs:
        return None
    mapa = {str(k).lower(): v for k, v in attrs.items()}
    for nome in nomes:
        if nome and str(nome).lower() in mapa:
            return mapa[str(nome).lower()]
    return None


def _normalizar_sim_nao(valor):
    """Converte respostas comuns de Survey123 para True/False quando possível."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return valor
    texto = str(valor).strip().lower()
    texto = (
        texto.replace("ã", "a")
        .replace("á", "a")
        .replace("à", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )
    if texto in {"sim", "s", "yes", "y", "true", "1"}:
        return True
    if texto in {"nao", "n", "no", "false", "0"}:
        return False
    return None


def _resumir_anexos_survey123(anexos):
    """Resumo seguro dos anexos, sem URL e sem token, para diagnosticar o payload."""
    resumo = []
    for i, anexo in enumerate(anexos or []):
        if not isinstance(anexo, dict):
            continue
        resumo.append(
            {
                "i": i,
                "campo": anexo.get("campo"),
                "name": anexo.get("name"),
                "contentType": anexo.get("contentType"),
                "size": anexo.get("size"),
                "id": anexo.get("id") or anexo.get("attachmentId") or anexo.get("objectId"),
                "keywords": anexo.get("keywords"),
                "url_presente": bool(anexo.get("url")),
            }
        )
    return resumo


def _campo_anexo_normalizado(anexo: dict):
    bruto = anexo.get("campo") or anexo.get("keywords") or ""
    return str(bruto).strip().lower()


def _nome_anexo_normalizado(anexo: dict):
    return str(anexo.get("name") or "").strip().lower()


def _parece_anexo_sim(anexo: dict):
    campo = _campo_anexo_normalizado(anexo)
    nome = _nome_anexo_normalizado(anexo)
    return (
        campo in {"fatura_cemig_sim", "conta_cemig_sim", "fatura_sim", "cemig_sim", "sim"}
        or "cemig_sim" in campo
        or "fatura_sim" in campo
        or "conta_sim" in campo
        or "cemig sim" in nome
        or "cemig_sim" in nome
        or "fatura_sim" in nome
    )


def _selecionar_anexos_gd_survey123(anexos):
    """Seleciona anexo principal e anexo SIM sem processar a SIM ainda.

    A conta principal continua sendo processada como antes. A fatura SIM é apenas
    localizada e registrada em log nesta RC13.1, para confirmar o formato do payload.
    """
    anexos = [a for a in (anexos or []) if isinstance(a, dict)]
    campos_principal = {
        "conta",
        "conta_cemig",
        "fatura_cemig",
        "foto_conta",
        "anexo_conta",
        "arquivo_conta",
    }

    anexo_sim = None
    for anexo in anexos:
        if _parece_anexo_sim(anexo):
            anexo_sim = anexo
            break

    anexo_principal = None
    for anexo in anexos:
        campo = _campo_anexo_normalizado(anexo)
        if campo in campos_principal and not _parece_anexo_sim(anexo):
            anexo_principal = anexo
            break

    if anexo_principal is None:
        for anexo in anexos:
            if not _parece_anexo_sim(anexo):
                anexo_principal = anexo
                break

    if anexo_principal is None and anexos:
        anexo_principal = anexos[0]

    return {
        "principal": anexo_principal,
        "sim": anexo_sim,
        "principal_campo": _campo_anexo_normalizado(anexo_principal) if anexo_principal else None,
        "sim_campo": _campo_anexo_normalizado(anexo_sim) if anexo_sim else None,
    }

def _adicionar_token_url(url: str, token: str | None):
    if not token:
        return url
    partes = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(partes.query, keep_blank_values=True))
    query["token"] = token
    nova_query = urllib.parse.urlencode(query)
    return urllib.parse.urlunsplit((partes.scheme, partes.netloc, partes.path, nova_query, partes.fragment))


def _baixar_anexo_survey123(anexo: dict, token: str | None) -> Path:
    url = anexo.get("url")
    nome = anexo.get("name") or f"attachment_{anexo.get('id', 'arquivo')}.jpg"
    sufixo = Path(nome).suffix.lower() or ".jpg"
    if sufixo not in FORMATOS_SUPORTADOS:
        # Para o OCR atual, preservamos os formatos já homologados.
        raise HTTPException(status_code=415, detail=f"Formato do anexo não suportado: {sufixo}")
    if not url:
        raise HTTPException(status_code=400, detail="Anexo sem URL no payload do Survey123.")

    url_download = _adicionar_token_url(url, token)
    fd, nome_tmp = tempfile.mkstemp(prefix="survey123_anexo_", suffix=sufixo, dir="/tmp")
    os.close(fd)
    caminho = Path(nome_tmp)

    req = urllib.request.Request(url_download, headers={"User-Agent": "cemig-ocr-api/1.0.0-RC13.1.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            dados = resp.read(MAX_UPLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Erro HTTP ao baixar anexo ArcGIS: {exc.code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao baixar anexo ArcGIS: {type(exc).__name__}: {exc}")

    if len(dados) > MAX_UPLOAD_BYTES:
        caminho.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Anexo excede o limite de 4 MB desta API.")
    if not dados:
        caminho.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Anexo baixado vazio.")

    caminho.write_bytes(dados)
    return caminho


def _arcgis_request_json(url: str, params: dict | None = None, timeout: int = 60):
    query = urllib.parse.urlencode(params or {})
    url_final = url + (("&" if "?" in url else "?") + query if query else "")
    req = urllib.request.Request(url_final, headers={"User-Agent": "cemig-ocr-api/1.0.0-RC13.1.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            texto = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        raise HTTPException(status_code=502, detail=f"Erro HTTP ArcGIS GET {exc.code}: {detalhe[:500]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ArcGIS GET: {type(exc).__name__}: {exc}")

    try:
        return json.loads(texto)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Resposta ArcGIS não é JSON: {texto[:500]}")


def _arcgis_post_form_json(url: str, data: dict, timeout: int = 90):
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "cemig-ocr-api/1.0.0-RC13.1.1",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            texto = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        raise HTTPException(status_code=502, detail=f"Erro HTTP ArcGIS POST {exc.code}: {detalhe[:500]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ArcGIS POST: {type(exc).__name__}: {exc}")

    try:
        return json.loads(texto)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Resposta ArcGIS não é JSON: {texto[:500]}")


def _converter_data_arcgis(valor):
    if not valor:
        return None
    if isinstance(valor, (int, float)):
        return valor
    texto = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(texto, fmt).replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    return valor


def _obter_metadados_layer(service_url: str, layer_id, token: str | None):
    if not service_url:
        return {}
    url = f"{service_url.rstrip('/')}/{layer_id}"
    params = {"f": "json"}
    if token:
        params["token"] = token
    dados = _arcgis_request_json(url, params=params, timeout=60)
    if isinstance(dados, dict) and dados.get("error"):
        raise HTTPException(status_code=502, detail={"erro_arcgis_metadata": dados.get("error")})
    return dados if isinstance(dados, dict) else {}


def _campo_tipo(metadata: dict):
    campos = {}
    for field in metadata.get("fields", []) or []:
        nome = field.get("name")
        if nome:
            campos[nome.lower()] = field.get("type")
    return campos


def _adaptar_valor_arcgis(nome_campo: str, valor, tipos: dict):
    tipo = tipos.get(nome_campo.lower())
    if valor is None:
        return None
    if tipo == "esriFieldTypeDate":
        return _converter_data_arcgis(valor)
    if tipo in {"esriFieldTypeInteger", "esriFieldTypeSmallInteger", "esriFieldTypeOID", "esriFieldTypeBigInteger"}:
        if isinstance(valor, bool):
            return 1 if valor else 0
        try:
            return int(valor)
        except Exception:
            return valor
    if tipo in {"esriFieldTypeDouble", "esriFieldTypeSingle"}:
        try:
            return float(valor)
        except Exception:
            return valor
    if tipo == "esriFieldTypeString":
        if isinstance(valor, bool):
            return "Sim" if valor else "Não"
        return str(valor)
    return valor



def _sim_nao_texto(valor):
    if valor is True:
        return "Sim"
    if valor is False:
        return "Não"
    return None


def _normalizar_ref_gd(valor):
    try:
        from ocr_engine import normalizar_referencia_mes
        return normalizar_referencia_mes(valor)
    except Exception:
        if not valor:
            return None
        return str(valor).strip().upper()


def _normalizar_uc_gd(valor):
    try:
        from ocr_engine import normalizar_unidade_consumidora
        return normalizar_unidade_consumidora(valor)
    except Exception:
        if not valor:
            return None
        return "".join(ch for ch in str(valor) if ch.isdigit()) or None


def _montar_contexto_gd(resultado: dict, resultado_sim: dict | None, eh_gd: bool | None, anexo_sim_presente: bool):
    """Compara conta principal x fatura SIM e gera campos de conferência/observação."""
    obs = []

    gd_detectado_api = resultado.get("gdDetectado")
    if gd_detectado_api is None:
        gd_detectado_api = True if resultado_sim else None

    if eh_gd is True and not anexo_sim_presente:
        obs.append("Conta marcada como GD, mas a fatura CEMIG SIM não foi anexada/localizada.")

    if gd_detectado_api is True and eh_gd is not True and not anexo_sim_presente:
        obs.append("Possível conta GD detectada pela API, mas a fatura CEMIG SIM não foi anexada.")

    unidade_confere = None
    referencia_confere = None

    if resultado_sim:
        identificador = resultado.get("identificador") or {}
        unidade_principal = identificador.get("valor") if isinstance(identificador, dict) else None
        unidade_sim = resultado_sim.get("unidadeConsumidora")
        uc_principal_norm = _normalizar_uc_gd(unidade_principal)
        uc_sim_norm = _normalizar_uc_gd(unidade_sim)

        if uc_principal_norm and uc_sim_norm:
            unidade_confere = uc_principal_norm == uc_sim_norm
            if not unidade_confere:
                obs.append(
                    f"Divergência: unidade consumidora da fatura SIM ({unidade_sim}) não confere com a conta principal ({unidade_principal})."
                )

        ref_principal = resultado.get("referencia")
        ref_sim = resultado_sim.get("mesReferencia") or resultado_sim.get("mesReferenciaCanonica")
        ref_principal_norm = _normalizar_ref_gd(ref_principal)
        ref_sim_norm = _normalizar_ref_gd(ref_sim)

        if ref_principal_norm and ref_sim_norm:
            referencia_confere = ref_principal_norm == ref_sim_norm
            if not referencia_confere:
                obs.append(
                    f"Divergência: mês de referência da fatura SIM ({ref_sim}) não confere com a conta principal ({ref_principal})."
                )

    return {
        "gd_detectado_api": gd_detectado_api,
        "sim_unidade_confere": unidade_confere,
        "sim_referencia_confere": referencia_confere,
        "observacao_processamento": " ".join(obs) if obs else None,
    }


def _mapear_resultado_sim_para_feature(resultado_sim: dict | None):
    if not resultado_sim:
        return {}
    return {
        "sim_razao_social": resultado_sim.get("razaoSocial"),
        "sim_cnpj": resultado_sim.get("cnpj"),
        "sim_endereco_instalacao": resultado_sim.get("enderecoInstalacao"),
        "sim_unidade_consumidora": resultado_sim.get("unidadeConsumidora"),
        "sim_mes_referencia": resultado_sim.get("mesReferencia"),
        "sim_numero_documento": resultado_sim.get("numeroDocumento"),
        "sim_data_emissao": resultado_sim.get("dataEmissao"),
        "sim_plano_contratado": resultado_sim.get("planoContratado"),
        "sim_percentual_contratado": resultado_sim.get("percentualContratado"),
        "sim_referencia_contratacao_kwh": resultado_sim.get("referenciaContratacaoKwh"),
        "sim_energia_gerada_kwh": resultado_sim.get("energiaGeradaKwh"),
        "sim_energia_compensada_kwh": resultado_sim.get("energiaCompensadaKwh"),
        "sim_saldo_gerado_mes_kwh": resultado_sim.get("saldoGeradoMesKwh"),
        "sim_saldo_acumulado_kwh": resultado_sim.get("saldoAcumuladoKwh"),
        "sim_valor_unitario": resultado_sim.get("valorUnitario"),
        "sim_valor_total": resultado_sim.get("valorTotal"),
        "sim_data_vencimento": resultado_sim.get("dataVencimento"),
        "sim_economia_valor": resultado_sim.get("economiaValor"),
        "sim_co2_evitado_kg": resultado_sim.get("co2EvitadoKg"),
        "sim_status": resultado_sim.get("status") or "Processado",
    }


def _mapear_resultado_para_feature(resultado: dict, roteamento: dict, tempos: dict, resultado_sim: dict | None = None, gd_contexto: dict | None = None):
    endereco = resultado.get("endereco") or {}
    identificador = resultado.get("identificador") or {}
    gd_contexto = gd_contexto or {}

    dados = {
        "nome": resultado.get("nome"),
        "logradouro": endereco.get("logradouro"),
        "numero": endereco.get("numero"),
        "complemento": endereco.get("complemento"),
        "bairro": endereco.get("bairro"),
        "cep": endereco.get("cep"),
        "cidade": endereco.get("cidade"),
        "uf": endereco.get("uf"),
        "unidade_consumidora": identificador.get("valor") if isinstance(identificador, dict) else None,
        "referencia": resultado.get("referencia"),
        "vencimento": resultado.get("vencimento"),
        "valor": resultado.get("valor"),
        "consumo_kwh": resultado.get("consumoKWh"),
        "irpj": resultado.get("impostoRetidoIRPJ"),
        "valor_validado": resultado.get("valorValidado"),
        "motor": roteamento.get("motor_escolhido") or roteamento.get("motor"),
        "tempo_processamento": tempos.get("total_s"),
        "versao_api": "1.0.0-RC13.1.1",
        "status": "Processado",
        "gd_detectado_api": _sim_nao_texto(gd_contexto.get("gd_detectado_api")),
        "observacao_processamento": gd_contexto.get("observacao_processamento"),
        "sim_unidade_confere": _sim_nao_texto(gd_contexto.get("sim_unidade_confere")),
        "sim_referencia_confere": _sim_nao_texto(gd_contexto.get("sim_referencia_confere")),
    }
    dados.update(_mapear_resultado_sim_para_feature(resultado_sim))
    return dados


def _atualizar_feature_layer_survey123(info: dict, resultado: dict, roteamento: dict, tempos: dict, payload: dict, resultado_sim: dict | None = None, gd_contexto: dict | None = None):
    service_url = info.get("featureServiceUrl")
    layer_id = info.get("layerId") if info.get("layerId") is not None else 0
    object_id = info.get("objectId")
    token = info.get("portalToken")

    if not service_url:
        raise HTTPException(status_code=400, detail="featureServiceUrl ausente no payload do Survey123.")
    if object_id is None:
        raise HTTPException(status_code=400, detail="objectId ausente no payload do Survey123.")
    if not token:
        raise HTTPException(status_code=400, detail="portalInfo.token ausente no payload do Survey123.")

    metadata = _obter_metadados_layer(service_url, layer_id, token)
    tipos = _campo_tipo(metadata)
    nomes_campos_lower = set(tipos.keys())

    object_id_field = None
    try:
        object_id_field = (((payload.get("feature") or {}).get("layerInfo") or {}).get("objectIdField"))
    except Exception:
        object_id_field = None
    object_id_field = object_id_field or metadata.get("objectIdField") or "objectid"

    atributos = {object_id_field: object_id}
    mapeados = _mapear_resultado_para_feature(resultado, roteamento, tempos, resultado_sim=resultado_sim, gd_contexto=gd_contexto)
    for campo, valor in mapeados.items():
        if not nomes_campos_lower or campo.lower() in nomes_campos_lower:
            atributos[campo] = _adaptar_valor_arcgis(campo, valor, tipos)

    url_update = f"{service_url.rstrip('/')}/{layer_id}/updateFeatures"
    resposta = _arcgis_post_form_json(
        url_update,
        {
            "f": "json",
            "token": token,
            "features": json.dumps([{"attributes": atributos}], ensure_ascii=False, default=str),
        },
        timeout=90,
    )

    if isinstance(resposta, dict) and resposta.get("error"):
        raise HTTPException(status_code=502, detail={"erro_arcgis_update": resposta.get("error")})

    return {
        "url": url_update,
        "objectId": object_id,
        "objectIdField": object_id_field,
        "attributes": _mascarar_tokens(atributos),
        "response": resposta,
    }


@app.options("/webhook/survey123")
async def webhook_survey123_options():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )



def _processar_survey123_em_background(payload: dict, meta: dict | None = None):
    """Executa o fluxo pesado após responder rapidamente ao Survey123.

    RC13.1: processa a conta CEMIG principal e, quando existir, a segunda fatura
    CEMIG SIM do fluxo de Geração Distribuída, atualizando o mesmo registro.
    """
    inicio = time.perf_counter()
    caminho = None
    caminho_sim = None
    meta = meta or {}
    info = {}

    try:
        info = _extrair_info_survey123(payload)
        anexos = info.get("attachments") or []
        object_id = info.get("objectId")
        global_id = info.get("globalId")
        layer_id = info.get("layerId")

        eh_gd_raw = _ler_campo_formulario(payload, "eh_gd", "geracao_distribuida", "conta_gd")
        eh_gd = _normalizar_sim_nao(eh_gd_raw)
        resumo_anexos = _resumir_anexos_survey123(anexos)

        print(
            f"[SURVEY123 RC13.1] início objectId={object_id} globalId={global_id} "
            f"layerId={layer_id} eh_gd_raw={eh_gd_raw!r} eh_gd={eh_gd} "
            f"anexos={len(anexos)} bytes={meta.get('body_bytes')}",
            flush=True,
        )
        print(
            f"[SURVEY123 RC13.1] anexos_payload objectId={object_id} "
            f"resumo={json.dumps(resumo_anexos, ensure_ascii=False)}",
            flush=True,
        )

        if not anexos:
            print(f"[SURVEY123 RC13.1] erro objectId={object_id}: nenhum anexo encontrado", flush=True)
            return

        selecao_anexos = _selecionar_anexos_gd_survey123(anexos)
        anexo = selecao_anexos.get("principal")
        anexo_sim = selecao_anexos.get("sim")

        print(
            f"[SURVEY123 RC13.1] selecao_anexos objectId={object_id} "
            f"principal_campo={selecao_anexos.get('principal_campo')} "
            f"principal_nome={anexo.get('name') if anexo else None} "
            f"sim_encontrada={bool(anexo_sim)} "
            f"sim_campo={selecao_anexos.get('sim_campo')} "
            f"sim_nome={anexo_sim.get('name') if anexo_sim else None}",
            flush=True,
        )

        if not anexo:
            print(f"[SURVEY123 RC13.1] erro objectId={object_id}: anexo principal não localizado", flush=True)
            return

        print(
            f"[SURVEY123 RC13.1] baixando anexo principal objectId={object_id} "
            f"campo={selecao_anexos.get('principal_campo')} "
            f"nome={anexo.get('name')} tipo={anexo.get('contentType')} tamanho={anexo.get('size')}",
            flush=True,
        )

        caminho = _baixar_anexo_survey123(anexo, info.get("portalToken"))
        print(f"[SURVEY123 RC13.1] anexo principal baixado objectId={object_id} bytes={caminho.stat().st_size}", flush=True)

        inicio_ocr = time.perf_counter()
        motor, roteamento = _obter_motor_para_documento(caminho)
        from ocr_engine import process_document, process_document_sim
        resultado, tempos = process_document(caminho, motor, save_debug=False)
        tempo_ocr_total = round(time.perf_counter() - inicio_ocr, 4)

        resultado_sim = None
        tempos_sim = None
        roteamento_sim = None

        if anexo_sim:
            print(
                f"[SURVEY123 RC13.1] baixando fatura SIM objectId={object_id} "
                f"campo={selecao_anexos.get('sim_campo')} "
                f"nome={anexo_sim.get('name')} tipo={anexo_sim.get('contentType')} tamanho={anexo_sim.get('size')}",
                flush=True,
            )
            caminho_sim = _baixar_anexo_survey123(anexo_sim, info.get("portalToken"))
            print(f"[SURVEY123 RC13.1] fatura SIM baixada objectId={object_id} bytes={caminho_sim.stat().st_size}", flush=True)

            inicio_sim = time.perf_counter()
            try:
                resultado_sim, tempos_sim = process_document_sim(caminho_sim, ocr=None, save_debug=False)
                roteamento_sim = {"motor_escolhido": "pdf_text_direto_sim", "layout": "cemig_sim"}
            except RuntimeError:
                motor_sim, roteamento_sim = _obter_motor_para_documento(caminho_sim)
                resultado_sim, tempos_sim = process_document_sim(caminho_sim, ocr=motor_sim, save_debug=False)

            print(
                f"[SURVEY123 RC13.1] fatura SIM processada objectId={object_id} "
                f"razao={resultado_sim.get('razaoSocial') if resultado_sim else None} "
                f"uc={resultado_sim.get('unidadeConsumidora') if resultado_sim else None} "
                f"ref={resultado_sim.get('mesReferencia') if resultado_sim else None} "
                f"valor={resultado_sim.get('valorTotal') if resultado_sim else None} "
                f"sim_s={round(time.perf_counter() - inicio_sim, 4)}",
                flush=True,
            )
        elif eh_gd is True:
            print(
                f"[SURVEY123 RC13.1] alerta_gd objectId={object_id}: "
                "formulário marcou GD=Sim, mas a fatura CEMIG SIM não foi localizada no payload",
                flush=True,
            )

        gd_contexto = _montar_contexto_gd(
            resultado,
            resultado_sim,
            eh_gd=eh_gd,
            anexo_sim_presente=bool(anexo_sim),
        )

        update_result = _atualizar_feature_layer_survey123(
            info,
            resultado,
            roteamento,
            tempos,
            payload,
            resultado_sim=resultado_sim,
            gd_contexto=gd_contexto,
        )
        sucesso_update = bool(
            isinstance(update_result.get("response"), dict)
            and update_result["response"].get("updateResults", [{}])[0].get("success")
        )

        print(
            f"[SURVEY123 RC13.1] processado objectId={object_id} "
            f"nome={resultado.get('nome')} ref={resultado.get('referencia')} "
            f"valor={resultado.get('valor')} validado={resultado.get('valorValidado')} "
            f"sim_processada={bool(resultado_sim)} "
            f"sim_valor={resultado_sim.get('valorTotal') if resultado_sim else None} "
            f"uc_confere={gd_contexto.get('sim_unidade_confere')} "
            f"ref_confere={gd_contexto.get('sim_referencia_confere')} "
            f"motor={roteamento.get('motor_escolhido') or roteamento.get('motor')} "
            f"ocr_s={tempo_ocr_total} update={sucesso_update} "
            f"total_s={round(time.perf_counter() - inicio, 4)}",
            flush=True,
        )

    except Exception as exc:
        print(
            f"[SURVEY123 RC13.1] erro objectId={info.get('objectId') if isinstance(info, dict) else None}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        print(traceback.format_exc(limit=8), flush=True)
    finally:
        for item in (caminho, caminho_sim):
            if item is not None:
                try:
                    item.unlink(missing_ok=True)
                except Exception:
                    pass


@app.post("/webhook/survey123")
async def survey123_webhook(request: Request, background_tasks: BackgroundTasks):
    inicio = time.perf_counter()
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace")

    try:
        payload = json.loads(body_text) if body_text else {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Payload não é JSON válido: {type(exc).__name__}: {exc}")

    info = _extrair_info_survey123(payload)
    anexos = info.get("attachments") or []
    eh_gd_raw = _ler_campo_formulario(payload, "eh_gd", "geracao_distribuida", "conta_gd")
    eh_gd = _normalizar_sim_nao(eh_gd_raw)
    resumo_anexos = _resumir_anexos_survey123(anexos)

    print(
        f"[SURVEY123 RC13.1] recebido objectId={info.get('objectId')} "
        f"globalId={info.get('globalId')} layerId={info.get('layerId')} "
        f"eh_gd_raw={eh_gd_raw!r} eh_gd={eh_gd} "
        f"anexos={len(anexos)} body_bytes={len(body)}",
        flush=True,
    )
    print(
        f"[SURVEY123 RC13.1] anexos_recebidos objectId={info.get('objectId')} "
        f"resumo={json.dumps(resumo_anexos, ensure_ascii=False)}",
        flush=True,
    )
    _logar_diagnostico_detalhado_anexos("[SURVEY123 RC13.1 POST]", info.get('objectId'), payload, info)

    background_tasks.add_task(
        _processar_survey123_em_background,
        payload,
        {"body_bytes": len(body)},
    )

    return {
        "status": "accepted",
        "versao": "1.0.0-RC13.1.1",
        "mensagem": "Webhook recebido. Processamento OCR iniciado em background.",
        "objectId": info.get("objectId"),
        "globalId": info.get("globalId"),
        "attachments": len(anexos),
        "eh_gd_raw": eh_gd_raw,
        "eh_gd": eh_gd,
        "anexos_resumo": resumo_anexos,
        "tempo_resposta_s": round(time.perf_counter() - inicio, 4),
    }
