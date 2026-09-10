# CHANGELOG

## 1.0.0-RC7
- Refinamento da extração de Consumo kWh por posição visual da coluna.
- Evita confundir consumo com Leitura Anterior/Atual.
- Mantém endpoint OCR e webhook Survey123 compatíveis.


## 1.0.0-RC6

- Adiciona extração de `consumoKWh` na API OCR.
- Envia `consumo_kwh` para a Feature Layer quando o campo existir.
- Mantém endpoints e fluxo Survey123 existentes.

# Changelog

## 1.0.0-RC5

- Limpeza dos logs do webhook Survey123.
- Mantido processamento em background.
- Mantida atualização automática da Feature Layer.
- Removido log de payload completo, headers e token.

v1.0.0-RC3 - Webhook Survey123 baixa anexo e executa OCR; sem update da Feature Layer.
