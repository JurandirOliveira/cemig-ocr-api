# v1.0.0-RC13.3 - Detecção GD na conta principal

- Adicionada detecção de Geração Distribuída na conta CEMIG comum.
- A API agora reconhece sinais como `Energia compensada GD`, `Energia SCEE`, `Saldo atual de geração` e `Unidade faz parte de sistema de compensação de energia`.
- Quando o usuário marcar GD = Não, mas a conta principal indicar GD, a API grava `gd_detectado_api = Sim` e preenche `observacao_processamento` avisando que a fatura CEMIG SIM não foi anexada.
- Mantidas as correções da RC13.2 para NF3e/Reimpressão e da RC13.1 para normalização de mês.

# Changelog

## 1.0.0-RC13.2 - Correção NF3e/Reimpressão GD

- Corrige a extração de nome e endereço no layout CEMIG comum NF3e/Reimpressão quando o nome do cliente contém números, como "POSTO SAUDE H BICALHO 480".
- O parser NF3e agora reconhece também documentos com o rótulo "N.º da unidade consumidora", além de "Nº da instalação".
- Prioriza o bloco do consumidor e ignora o cabeçalho institucional da CEMIG para evitar capturar "REIMPRESSÃO" ou "AV. BARBACENA" como dados do cliente.
- Mantém intacta a lógica GD/SIM da RC13.1: segundo anexo `fatura_cemig_sim`, parser CEMIG SIM, comparação de unidade e normalização do mês de referência.

## 1.0.0-RC13.1 - Normalização de mês GD/SIM

- Ajuste pontual na comparação entre a referência da conta CEMIG comum e a referência da fatura CEMIG SIM.
- Agora formatos equivalentes como `AGO/2026`, `Agosto/2026`, `AGOSTO DE 2026` e `08/2026` são normalizados internamente para `2026-08`.
- Os valores originais extraídos continuam sendo gravados nos campos do Survey123; a normalização é usada apenas para evitar divergência falsa.
- Mantém todo o comportamento da RC13 para webhook, OCR da conta comum, OCR da fatura CEMIG SIM e atualização da Feature Layer.

# CEMIG OCR API

## 1.0.0-RC13 - OCR da Fatura CEMIG SIM / GD

- Mantém o OCR da conta CEMIG principal igual à RC12.1/RC11.
- Processa o segundo anexo `fatura_cemig_sim` quando enviado pelo Survey123.
- Adiciona parser dedicado para o layout CEMIG SIM, com extração direta de texto em PDF digital e fallback para OCR em imagem/PDF escaneado.
- Grava os campos `sim_*` na mesma Feature Layer do formulário.
- Compara unidade consumidora e mês de referência entre a conta principal e a fatura SIM.
- Preenche `observacao_processamento` quando houver divergência ou quando a conta for marcada como GD sem anexo SIM.
- Adiciona endpoint de teste `/ocr/fatura-cemig-sim`.

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
