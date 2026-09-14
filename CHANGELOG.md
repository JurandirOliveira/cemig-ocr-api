# CEMIG OCR API

## 1.0.0-RC12 - Diagnóstico GD / CEMIG SIM

- Mantém o OCR da conta CEMIG principal igual à RC11.
- Adiciona diagnóstico no webhook Survey123 para ler o campo `eh_gd`.
- Lista nos logs os anexos recebidos por campo, sem expor URL nem token.
- Seleciona o anexo principal separadamente da possível fatura `fatura_cemig_sim`.
- Ainda não processa a fatura CEMIG SIM; esta versão confirma o payload antes do parser SIM.


## 1.0.0-RC11

- Ajuste da regra de validação do valor:
  - com linha digitável/código de barras: valida somente quando topo, rodapé e código são iguais;
  - sem linha digitável/código de barras: valida quando topo e rodapé são iguais.
- Mantido o OCR, parser de campos e integração Survey123.

# Changelog

## 1.0.0-RC9
- Refinamento da extração de `consumoKWh` para layouts com tabela de Informações Técnicas.
- Prioriza cálculo por linha técnica: `(Leitura Atual - Leitura Anterior) x Constante de Multiplicação`.
- Trata leituras com ponto de milhar como `3.764` e `3.797`.
- Mantém compatibilidade com Survey123 webhook, OCR e atualização da Feature Layer.

## 1.0.0-RC8
- Correção inicial da extração de Consumo kWh por linha técnica.


## v1.0.0-RC10

- Adicionado suporte ao layout eletrônico/NF3e da CEMIG por extração direta de texto em PDF.
- Captura de nome, endereço, instalação, referência, vencimento, valor, IRPJ e consumo kWh nesse layout.
