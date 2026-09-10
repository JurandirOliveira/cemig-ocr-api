# Changelog

## 1.0.0-RC9
- Refinamento da extração de `consumoKWh` para layouts com tabela de Informações Técnicas.
- Prioriza cálculo por linha técnica: `(Leitura Atual - Leitura Anterior) x Constante de Multiplicação`.
- Trata leituras com ponto de milhar como `3.764` e `3.797`.
- Mantém compatibilidade com Survey123 webhook, OCR e atualização da Feature Layer.

## 1.0.0-RC8
- Correção inicial da extração de Consumo kWh por linha técnica.
