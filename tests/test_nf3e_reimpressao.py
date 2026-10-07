import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import fitz

ROOT = Path(__file__).resolve().parents[1]
# Parser tests do not download or initialize neural models.
spec = importlib.util.spec_from_file_location('engine_under_test', ROOT / 'ocr_engine.py')
engine = importlib.util.module_from_spec(spec)
stub = types.ModuleType('paddleocr')
stub.PaddleOCR = object
with patch.dict(sys.modules, {'paddleocr': stub}):
    spec.loader.exec_module(engine)

LINES = [
    'DOCUMENTO AUXILIAR DA NOTA FISCAL DE ENERGIA ELÉTRICA ELETRÔNICA',
    'CEMIG DISTRIBUIÇÃO S.A.',
    'UNIDADE DE TESTE 512', 'RUA DE TESTE 25 CO', 'CENTRO',
    '34000-099 NOVA LIMA, MG', 'CNPJ 00.000.0**/****-**',
    '3.477.274.018-46', 'JUN/2026 22/07/2026 210,59',
    'Energia kWh ARN225056605 25.196 25.905 1 709',
    'Energia Elétrica kWh 161 1,17256261 188,46',
    'Energia SCEE s/ ICMS kWh 548 0,64360349 352,84',
    'Energia compensada GD I kWh 548 0,59855125 -328,15',
    'Imposto Retido - IRPJ -2,56', 'TOTAL 210,59 35,50 188,46 33,92',
    'JUN/26 709 22,15 32',
    '000032634834 3.477.274.018-46', '22/07/2026 R$210,59', 'Junho/2026',
]

class ReimpressaoTests(unittest.TestCase):
    def test_sem_rotulos_raster(self):
        r = engine._parse_nf3e_instalacao_from_lines(LINES)
        self.assertEqual(r['nome'], 'UNIDADE DE TESTE 512')
        self.assertEqual(r['identificador']['valor'], '3.477.274.018-46')
        self.assertEqual(r['valor'], 210.59)
        self.assertEqual(r['impostoRetidoIRPJ'], -2.56)
        self.assertEqual(r['consumoKWh'], 709)
        self.assertTrue(r['valorValidado'])

    def test_nao_valida_rodape_divergente(self):
        r = engine._parse_nf3e_instalacao_from_lines([x.replace('R$210,59', 'R$211,59') for x in LINES])
        self.assertFalse(r['valorValidado'])
        self.assertEqual(r['validacao']['ocr_rodape'], 211.59)

    def test_nao_valida_rodape_ausente(self):
        r = engine._parse_nf3e_instalacao_from_lines([x for x in LINES if 'R$' not in x])
        self.assertFalse(r['valorValidado'])
        self.assertIsNone(r['validacao']['ocr_rodape'])

    def test_irpj_nao_invade_total(self):
        r = engine._parse_nf3e_instalacao_from_lines([x.replace('IRPJ -2,56', 'IRPJ') for x in LINES])
        self.assertIsNone(r['impostoRetidoIRPJ'])

    def test_unidade_curta(self):
        r = engine._parse_nf3e_instalacao_from_lines([x.replace('3.477.274.018-46', '8.013.018-01') for x in LINES])
        self.assertEqual(r['identificador']['valor'], '8.013.018-01')

    def test_pdf_compilado_rejeitado(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'compilado.pdf'
            with fitz.open() as doc:
                doc.new_page(); doc.new_page(); doc.save(path)
            with self.assertRaisesRegex(ValueError, 'única conta'):
                engine.extract_pdf_text_layout(path)

if __name__ == '__main__':
    unittest.main()
