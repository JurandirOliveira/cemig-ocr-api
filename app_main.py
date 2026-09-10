import gc
import importlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="CEMIG OCR API - Diagnóstico Vercel",
    version="1.0.0-RC2-diagnostic",
    description="Diagnóstico incremental do runtime Vercel sem carregar OCR no startup.",
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
        "versao": "0.49.0",
        "mensagem": "FastAPI iniciou sem carregar Paddle/PaddleOCR.",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "versao": "0.49.0",
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
            "versao": "0.49.0",
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
    """Retorna uma versão curta e segura de um valor para logs/resposta."""
    try:
        texto = json.dumps(valor, ensure_ascii=False, default=str)
    except Exception:
        texto = str(valor)
    if len(texto) > limite:
        return texto[:limite] + "... [truncado]"
    return texto


def _buscar_chaves(obj, chaves_alvo, caminho="$", encontrados=None):
    """Busca recursivamente chaves no payload do Survey123, sem assumir formato fixo."""
    if encontrados is None:
        encontrados = []
    chaves_norm = {c.lower() for c in chaves_alvo}

    if isinstance(obj, dict):
        for chave, valor in obj.items():
            novo_caminho = f"{caminho}.{chave}"
            if str(chave).lower() in chaves_norm:
                encontrados.append({
                    "path": novo_caminho,
                    "key": chave,
                    "value": valor,
                })
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
        "layerId": ["layerId", "layerid", "layerID"],
        "attachments": ["attachments", "attachment", "attachmentInfos", "attachmentInfo", "adds", "updates"],
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


@app.post("/webhook/survey123")
async def survey123_webhook(request: Request):
    inicio = time.perf_counter()
    body = await request.body()
    body_text = body.decode("utf-8", errors="replace")

    try:
        payload = json.loads(body_text) if body_text else {}
        json_ok = True
        json_erro = None
    except Exception as exc:
        payload = {}
        json_ok = False
        json_erro = f"{type(exc).__name__}: {exc}"

    diagnostico, detalhes = _diagnosticar_payload_survey123(payload)

    print("", flush=True)
    print("=" * 90, flush=True)
    print("WEBHOOK SURVEY123 RECEBIDO", flush=True)
    print("=" * 90, flush=True)
    print(f"METHOD: {request.method}", flush=True)
    print(f"URL: {request.url}", flush=True)
    print(f"CLIENT: {request.client.host if request.client else None}", flush=True)
    print(f"BODY_BYTES: {len(body)}", flush=True)
    print(f"JSON_OK: {json_ok}", flush=True)
    if json_erro:
        print(f"JSON_ERRO: {json_erro}", flush=True)

    print("-" * 90, flush=True)
    print("HEADERS", flush=True)
    print(json.dumps(dict(request.headers), indent=2, ensure_ascii=False, default=str), flush=True)

    print("-" * 90, flush=True)
    print("DIAGNOSTICO CAMPOS-CHAVE", flush=True)
    print(json.dumps(diagnostico, indent=2, ensure_ascii=False, default=str), flush=True)

    print("-" * 90, flush=True)
    print("CAMINHOS ENCONTRADOS", flush=True)
    print(json.dumps(detalhes, indent=2, ensure_ascii=False, default=str), flush=True)

    print("-" * 90, flush=True)
    print("BODY BRUTO", flush=True)
    print(body_text, flush=True)

    if json_ok:
        print("-" * 90, flush=True)
        print("JSON FORMATADO", flush=True)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str), flush=True)

    print("=" * 90, flush=True)
    print("FIM WEBHOOK SURVEY123", flush=True)
    print("=" * 90, flush=True)
    print("", flush=True)

    return {
        "status": "ok",
        "versao": "1.0.0-RC2-diagnostic",
        "mensagem": "Webhook Survey123 recebido com sucesso.",
        "json_ok": json_ok,
        "diagnostico": {
            "objectId": _json_preview(diagnostico.get("objectId"), 300),
            "globalId": _json_preview(diagnostico.get("globalId"), 300),
            "featureServiceUrl": _json_preview(diagnostico.get("featureServiceUrl"), 300),
            "layerId": _json_preview(diagnostico.get("layerId"), 300),
            "attachments": _json_preview(diagnostico.get("attachments"), 300),
        },
        "tempo_s": round(time.perf_counter() - inicio, 4),
    }

