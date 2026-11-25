# cvdtools: pipeline CVD/DCVD

## Para usar (equipe)

Pré-requisitos, uma vez por máquina: Python 3.9 ou mais novo, [Git for Windows](https://git-scm.com/download/win), VS Code com a extensão Python, OrcaFlex instalado e acesso de leitura a este repositório no GitHub.

**1. Instalar ou atualizar o cvdtools** (terminal do VS Code; na primeira vez o Git pede o login do GitHub):

```bat
python -m pip install --upgrade "cvdtools[orcaflex] @ git+https://github.com/ThyagoCap/cvdtools.git"
```

**2. Preparar a pasta de projeto** (abra a pasta no VS Code, File > Open Folder, e rode no terminal dele):

```bat
python -m cvdtools setup
```

Num projeto que já tem uma pasta configurada (papéis, padrões e grade de mares acertados), copie a configuração dela, para toda a equipe partir do mesmo padrão:

```bat
python -m cvdtools setup --config-from "<pasta de projeto de referência>"
```

**Modelo base pelo GitHub (0.14.0):** a pasta `00.Base` pode ficar num repositório git privado próprio do projeto (com `00.Base/base_input.yml` ou `.dat`), em vez de ser distribuída à mão. Uma vez, na pasta de projeto de referência:

```bat
python -m cvdtools setup --base-repo "https://github.com/<conta>/<repositorio-da-base>.git"
```

O endereço fica no `cvd_project.json` (`base_repo`; `base_ref` opcional = branch ou tag) e o repositório é baixado em `.cvd_base\`. Cada `setup`/`update` traz a versão mais nova, e o build baixa sozinho na primeira vez. Quem faz `setup --config-from <pasta de referência>` herda o `base_repo`, então só precisa de acesso ao repositório (mesmo login do GitHub). A aba Operações pode continuar citando `00.Base/base_input.dat`: se só existir o `.yml` com o mesmo nome, ele é usado. A cópia em `.cvd_base` não deve ser editada (se for, o setup não a atualiza e avisa); mudanças no modelo base vão para o repositório.

**Atualizar depois:** `python -m cvdtools update` (na pasta de projeto) instala a versão do GitHub e roda o `setup`. Mesmo sem ele, os scripts se atualizam sozinhos na primeira vez que um roda depois de o cvdtools mudar.

O `setup` acha a planilha geral (aba "Input Sheet") e a pasta `00.Base` nesta pasta ou nas de cima e cria:
`cvd_project.json`, `cvd_config.xlsx`, `cvd_curves.xlsx`, a pasta `scripts/` (um script por etapa) e `.vscode/settings.json` (VS Code usa o Python em que o cvdtools foi instalado). Pode ser rodado de novo a qualquer momento, sobretudo depois de atualizar: ele acrescenta os parâmetros novos ao `cvd_config.xlsx` (com cópia `.bak`) e atualiza os scripts. Nada do usuário é apagado; script editado fica como está e a versão nova aparece ao lado como `.novo.py`.

**3. Usar:** em `scripts/00_caso.py`, preencha o ID do caso e salve; depois rode `01_conferir_inputs.py`, `02_montar_modelo.py`, `03_posicoes_estaticas.py`... com o botão Run Python File (canto superior direito do VS Code). As opções de cada etapa ficam no topo do script, em MAIÚSCULAS. Ctrl+C no terminal interrompe uma etapa. Detalhes em `scripts/LEIA-ME.md`.

Os scripts chamam `cvdtools.api` (uma função por etapa), que roda os mesmos comandos da linha de comando descritos abaixo. Quem preferir pode continuar usando `python -m cvdtools ...`.

## Para desenvolver

```bat
git clone https://github.com/ThyagoCap/cvdtools.git
cd cvdtools
python -m pip install -e ".[dev,orcaflex]"
python -m pytest -q -m "not orcaflex and not baseline"
```

- Uma versão nova: suba `version` em `pyproject.toml` e `src/cvdtools/__init__.py`, rode os testes, faça o commit e uma tag (`git tag v0.14.0 && git push --tags`). A equipe atualiza com o comando 1 (sem tag, pega o `main`; para fixar uma versão: `...cvdtools.git@v0.14.0`).
- Script novo para a equipe: um arquivo em `src/cvdtools/templates/scripts/` que chama uma função de `src/cvdtools/api.py`. O `setup` distribui.
- O GitHub Actions (`.github/workflows/tests.yml`) roda os testes sem OrcaFlex em cada push.
- Arquivos de projeto (`cvd_project.json`, `cvd_config.xlsx`, `cvd_curves.xlsx`, `casos/`, `scripts/`, `baseline/`) e arquivos do OrcaFlex nunca entram no git (`.gitignore`).

## Sobre esta biblioteca

Biblioteca interna que substitui, por fases, os scripts `_00`–`_18` da pasta-mãe (`_code_base`).
**A pasta-mãe não é alterada por nada daqui**: os scripts antigos só são lidos ou copiados para uma pasta temporária.

Plano completo: Projeto Claude "Pipeline CVD/DCVD" → `claude/plano_reestruturacao_pipeline_cvd.md`.

## Estado

| Fase | Conteúdo | Situação |
|---|---|---|
| 0 | Pacote, regressão, captura do baseline | pronta |
| 1 | Inputs: planilha geral + configuração → caso resolvido, validação, `case.json` | pronta |
| 2 | Núcleo: builder, checklist, geração de mares, extração de resultados | pronta |
| 3 | Estática: referência de puxada, solver, posições e varredura | pronta (casos com corcova) |

Fluxo resumido de um caso:

```bat
cvd inputs validate --out validacao.xlsx          :: o que falta/está incoerente na planilha geral
cvd inputs show "GLF49 2CVD MOBO New In"          :: valores resolvidos e de onde vieram
cvd build "GLF49 2CVD MOBO New In" --out <pasta>  :: modelo .dat + case.json + Checklist_<ID>.xlsx
cvd seastates "GLF49 2CVD MOBO New In" --base-dyn <modelo_dyn.dat> --out <pasta>\03.Dynamic\Irreg
:: (batch do OrcaFlex, como hoje)
cvd post --id "GLF49 2CVD MOBO New In" --sims <pasta dos .sim> --out results_irreg.xlsx
```

## Fases 1 e 2

### Progresso e logs

Todos os comandos que leem o projeto mostram o progresso no console, com hora e tempo decorrido. Cada cálculo de estática aparece numa linha (End B X e tração, ou puxada, ângulo e UF), com a duração.

| Opção | Mostra |
|---|---|
| (padrão) | etapas, premissas, cada cálculo de estática, avisos (estática que não converge, checklist com VERIFY/CHECK DEFAULT, curvas com problema) |
| `-v` | também cada sub-etapa da montagem e todas as linhas do checklist |
| `-q` | só avisos e erros |

Além do console, o log completo (nível `-v`) é gravado na pasta de saída: `cvd_build.log` (build), `cvd_statics.log` (statics positions), `<saída>.log` (statics sweep) e `cvd_seastates.log` (seastates). `Ctrl+C` cancela também uma estática em andamento: o pedido é repassado ao OrcaFlex pelo tratador de progresso da estática (`staticsProgressHandler`). Se a sua versão do OrcFxAPI não oferecer esse tratador, o cancelamento acontece quando a estática atual terminar.

**Estática final do build** (`model.final_statics`, padrão S): depois de montar e ajustar o End B, o `build` calcula a estática do modelo **completo** e grava as posições convergidas como forma inicial das linhas (`UseCalculatedPositions(SetLinesToUserSpecifiedStartingShape=True)`), para o `.dat` abrir já em equilíbrio. Se essa estática não convergir, o modelo é salvo sem as posições e a saída avisa. O `statics positions` também grava, ao lado de cada `.sim`, um `.dat` com as posições calculadas daquela posição.

Exemplo: `python -m cvdtools build "GLF27 2CVD MOBO New Out" --out "casos\GLF27" --overwrite -v`

### Arquivos do projeto (já criados nesta pasta)

- `cvd_project.json`: caminhos da planilha geral (`..\1327.planilha_geral_offline.xlsx`, só leitura), da configuração e da base dos modelos (`..`, então `00.Base\base_input.dat` é o modelo base atual). `analysis_root` ficou vazio de propósito: sem ele, `build`/`snapshot` exigem `--out` e nada é gravado nas pastas dos poços por engano.
- `cvd_config.xlsx`: gerada com `cvd config init --from-json ..\__INPUTS.json`. Abas:
  - **Defaults**: parâmetros que não existem na Input Sheet (LDA, solo, atrito, corcova, flutuadores, EF/BR EA/EIx, EIx/MBR dos cabos, simulação). Valores copiados do JSON do GLF-49. **Revise LDA e KPs da corcova por poço** (use Overrides).
  - **Overrides**: `ID | Parâmetro | Valor`; vence a Input Sheet. Exemplo: `GLF27 1DCVD PLET | line.eix | <nome da curva de histerese no modelo base>`.
  - **Operações**: `1st end DCVD` e `2nd end CVD` = com corcova, template `camelback`, suportadas; as demais ficam para a fase 5.
  - **Papéis**: nome de cada objeto no modelo base (`{LINE}` → `Line`, `{MCV}` → `MCV`, `{VESSEL}` → `Island Performer`...). Se um modelo base usa `MCV MOBO`, basta mudar aqui.
  - **PostProc / Critérios**: os 52 itens e 7 critérios do JSON, com nomes de objeto trocados por papéis. Os limites vieram **fixos** do GLF-49; para buscar o limite de cada caso na Input Sheet, preencha `Fonte do limite` (a coluna `Fonte sugerida` mostra a chave) e deixe `Limite` vazio.
  - **Checklist**: o que o checklist compara (input × propriedade OrcaFlex × tolerância).
  - **Mares**: grade do `cvd seastates` (padrão do `_10`: 0–180° a cada 30°, Tp 5–15 s, Hs 3,0 m).

Precedência na resolução de cada parâmetro: **Overrides > Input Sheet > Defaults**. O `case.json` e `cvd inputs show` dizem de onde veio cada valor.

### Premissas de modelagem (verificadas por caso)

Duas decisões variam de projeto para projeto e precisam ser explícitas. Ambas ficam na aba **Defaults** (valor do projeto) e podem ser trocadas por caso na aba **Overrides**. `cvd inputs show`, o `case.json` e a aba Info do checklist mostram o valor e a fonte de cada uma.

| Premissa | Parâmetro | Neste projeto (1349) | Efeito |
|---|---|---|---|
| Cabo do guincho em duas seções (guincho + extensão) | `model.winch_extension` (S/N) | **S** | **S**: o builder garante 2 seções, preserva o comprimento total e configura o tipo da extensão (`winch_ext.*` da Input Sheet passam a ser obrigatórios). **N**: junta as seções numa só. **Vazio**: aviso na validação e o modelo base fica como está, sem configurar a extensão (era o comportamento do `_01`). |
| Extremidades modeladas com MCV | `model.modelled_ends` (1 ou 2) | 1 (padrão) | Linhas mais curtas que `model.two_ends_max_length` (500 m) geram **aviso** pedindo confirmação, com sugestão dos IDs da outra extremidade (mesmo poço e mesma linha). Para confirmar 1 extremidade num caso, use o Override `model.modelled_ends = 1`. Para 2: `model.modelled_ends = 2`, `model.end_b_case_id = <ID da outra ponta>` (o MCV B vem dessa linha da Input Sheet; `mcv_b.*` aceita Override). `model.end_distance` é opcional (vazio = distância entre os hubs ajustada pela estática até a menor tração entre os BRs ser `model.end_b_tension_target`) e o "Modelo base (2 extremidades)" também (sem ele, MCV B e hub B são cópias do MCV e do hub). |

Desde a 0.8.0 o `cvd build` monta as **2 extremidades**: linha com o comprimento total, EF (1 segmento) e BR (20) nas duas pontas, MCV B fixo no hub B e distância ajustada pela estática (passo 3b do passo a passo no Obsidian). Desde a 0.10.0 os trechos dos BRs usam o tipo da linha flexível e o BR de cada ponta é um stiffener (como no modelo base), e o MCV B é configurado pelas próprias cotas (massa, volume, altura, CoG, CoV e inércia), nunca copiado do MCV A.

Configurações criadas antes da versão 0.3.0 recebem os parâmetros, papéis e linhas novos com `cvd config upgrade` (a configuração desta pasta já foi atualizada; uma cópia `.bak_<data>` é guardada a cada atualização). Exemplo: `cvd config upgrade --set model.winch_extension=S`.

### Curvas externas (histerese, vértebra de BR, cabo sintético)

Curvas que vêm de fora do OrcaFlex ficam numa **biblioteca de curvas** (`cvd_curves.xlsx`, declarada em `cvd_project.json` → `"curves"`). Cada curva é registrada uma vez e o builder a grava no modelo como *variable data*.

- **Aba `Índice`**: uma linha por curva, com as colunas `ID | Tipo | Load case | Aba | Unidade X | Unidade Y | Histerese (S/N) | MBL (kN) | Nome no OrcaFlex | Fonte (documento/rev) | Aliases | Comentário`.
- **Uma aba por curva**: coluna A = X e coluna B = Y, com cabeçalho na linha 1. Dá para colar direto do datasheet.
- **Tipos**:
  - `bending` (momento × curvatura): histerese da linha (`line.eix`) e momento resistente da vértebra (`br.eix`). Unidades: X em `1/m`, `1/mm`; Y em `kN.m`, `N.m`, `tf.m`.
  - `axial` (tração × deformação): cabo sintético ou extensão (`winch_ext.ea`, `winch_wire.ea`, `crane_wire.ea`). Unidades: X em `-`, `%`, `mm/m`; Y em `kN`, `N`, `tf`, `%MBL` (com a coluna MBL).
- **Como a Input Sheet (ou um Override) aponta para a curva**:
  - `curva:ID`;
  - `arquivo.xlsx#ID`, para outra planilha no mesmo formato (ex.: uma por fornecedor);
  - o texto antigo exato, cadastrado em `Aliases`. Assim `Histerese - WSI WSI 152.1524.xlsx [15]` continua funcionando sem editar a planilha geral.
- **Validação**: tipo compatível com o parâmetro, unidades conhecidas, X estritamente crescente, mínimo de 2 pontos, aliases sem conflito, aviso sem load case ou sem fonte. Uma referência não cadastrada vira ERRO em `cvd inputs validate`.
- **No builder**:
  - Se já existe no modelo uma curva com o "Nome no OrcaFlex", só a tabela é substituída.
  - Senão, a curva é criada copiando o tipo da curva-molde indicada nos papéis `CURVE_TEMPLATE_BENDING` (padrão: `Bend Restrictor Stiffness Curve`) e `CURVE_TEMPLATE_AXIAL`.
  - **O modelo base atual não tem curva axial**: crie uma qualquer (Variable data → axial stiffness) e informe o nome no papel `CURVE_TEMPLATE_AXIAL`. Sem isso, o builder tenta `ObjectType.AxialStiffness` (nome não confirmado na documentação) e, se não existir, para com mensagem.
  - Com `br.eix` = curva de vértebra, a curva "calculada" do `_01` (travamento + aço) deixa de ser usada.
- **No checklist**: o tipo de linha aponta para a curva certa (pelo nome) e, na linha "Curvas", a tabela no modelo é comparada ponto a ponto com a biblioteca (diferença relativa máxima = 0).
- **Comandos**:
  - `cvd curves check` lista e valida as curvas.
  - `cvd curves init --out x.xlsx [--from-sheet ABA --id ID --kind axial --x-col 2 --y-col 4 --first-row 3 --x-unit % --y-unit kN]` cria a planilha, opcionalmente importando uma curva tabelada de qualquer aba.

**Neste projeto**: `cvd_curves.xlsx` já traz `WINCH_EXT_AXIAL`, importada da aba `Winch Extension Stiffness` da planilha geral (39 pontos, 0–3,48 % × 0–3604 kN), com os aliases `Winch Extension Stiffness [10]/[13]/[15]`. **Confirme** se o número entre colchetes é load case (então são curvas diferentes) ou referência de documento (então a curva é a mesma). As histereses da WSI ainda precisam ser coladas.

### Sugestão da outra extremidade

Quando um caso vai usar duas extremidades (ou é uma linha curta que pede confirmação), a validação lista os casos da mesma linha (mesmo poço e descrição). A operação do "outro lado" vem primeiro, conforme a coluna `Outra extremidade (sugestão)` da aba Operações: 1st end DCVD ↔ 2nd end DCVD e 1st end CVD ↔ 2nd end CVD. É só sugestão; a escolha é sempre `model.end_b_case_id`.

### Inputs faltando: `missing_data.json` (0.13.0)

Se o `build` (script `02_montar_modelo.py`) encontra inputs obrigatórios faltando, ele para e grava `<pasta do caso>\missing_data.json`, um item por input com `"valor": null`, a descrição, a unidade, o valor atual e o destino. Preencha `valor` e rode de novo (com o `cvd_config.xlsx` fechado no Excel). A rodada seguinte grava os valores no `cvd_config.xlsx` (com `.bak`) e segue:

- **Defaults** (projeto inteiro): parâmetros sem coluna na Input Sheet, como LDA, atritos, EIx dos cabos e dimensões da corcova;
- **Overrides** (só este caso): o ID do MCV da outra extremidade (`model.end_b_case_id`; com ele, as cotas e a massa do MCV B vêm dessa linha da planilha geral, então elas não são pedidas uma a uma) e valores que deveriam vir da Input Sheet (a planilha geral é só leitura; corrigir lá também resolve). As sugestões do ID vêm da coluna `Other End ID` da planilha geral, se existir, e dos casos da mesma linha; nenhuma é aplicada sem o usuário escrever o valor.

Nada é preenchido sozinho: item deixado em `null` continua faltando, e o arquivo é gerado de novo só com o que falta. Valor inválido (texto em campo numérico, ID inexistente) não é gravado: volta no arquivo com o campo `erro`. JSON quebrado ou de outro caso não é sobrescrito. Sem pendências, o arquivo vira `missing_data.aplicado_<data>.json`, um registro do que foi gravado.

Com duas extremidades, o MCV B fica num constraint (`Constraint B`) com só o X livre, puxado para longe do MCV A por um tirante macio (`MCV B Pull`) com a tração `model.end_b_tension_target`. Uma estática (sem a corcova) já dá a distância de equilíbrio, e a linha não fica comprimida enquanto o solver procura a posição. `model.end_b_x_free`: `montagem` (padrão: livre só nesse ajuste; no modelo o X fica fixo na posição encontrada e o tirante sai), `sempre` (livre também na estática final, nas posições e na dinâmica) ou `nao` (MCV B ancorado e distância por busca, como até a 0.9.1). `model.end_b_pull_length` (200 m) é a distância até a âncora do tirante. Cada modelo de estática de ajuste é salvo antes de rodar em `<caso>\00.Montagem\` (`_ajuste_hubs`, `_ajuste_endB`, `_sem_corcova`), para conferir no OrcaFlex mesmo se não convergir; o `.dat` do caso é salvo montado antes de qualquer estática.

**Estáticas de convergência difícil** (0.9.3): `statics.robust` = `auto` (padrão) roda as estáticas da montagem e das posições com `statics.robust_max_iterations` = 50000, `statics.robust_min_damping` = 1000 e `statics.robust_max_damping` = 10000 direto nos casos de 2 extremidades; com 1 extremidade, só repete com esses valores se a estática normal falhar. Depois da convergência (e antes de salvar qualquer modelo) os valores do modelo voltam. Outras opções: `sempre`, `repetir`, `nunca`. **Escada de damping** (0.10.4): depois de convergir, as posições são gravadas e o damping desce ÷ `statics.robust_factor` = 10 (mesmas iterações) até o piso; se uma descida não convergir, volta ao último nível que convergiu. O damping só sobe (× 10, com o Jacobian perturbation factor em `statics.robust_jacobian` = 1e-3) quando a estática não converge, ou fica `statics.stall_seconds` = 120 s sem o erro cair à metade. Nas avaliações seguidas da busca de puxada (`statics positions`), cada estática começa no nível mais baixo que convergiu na anterior, com o Jacobiano do modelo (0.11.1: uma falha no início da busca não deixa mais as estáticas seguintes presas no teto 1e4/1e4). Com corcova, as estáticas com o masterlink fixo e com ele livre usam a mesma escada (0.13.1): a livre parte do nível em que a fixa convergiu, em vez de voltar a 1e3/1e4. `statics.robust_step_down` e `statics.robust_escalations` ficaram obsoletos. **Masterlink fixo primeiro** (0.10.5; 0.11.2): com corcova, a 1ª estática do `statics positions` roda antes com o masterlink fixo, grava as posições e só então libera o masterlink (`statics.masterlink_fixed_first` = sim). Depois que uma estática com o masterlink livre converge, o cabo muda só com ele livre. **Resíduo baixo parado** (0.11.2): se a estática para com resíduo < `statics.low_residual` = 0,01, o Jacobian perturbation factor sobe × 10 (0 → 1e-3 → 1e-2 → 0,1, `statics.robust_jacobian_max`) com o mesmo damping; o damping só sobe com resíduo alto ou Jacobiano no máximo. **Passos de cabo e erro alto** (0.11.3): o comprimento do cabo muda no máximo `statics.max_length_step` = 0,5 m por estática (mudanças maiores viram estáticas intermediárias, cada uma partindo da anterior); uma tentativa com "latest error" acima de `statics.max_error` = 1e5 (1e4 na 0.11.3) é cancelada e tratada como não convergida (sobe o damping). **Ângulo e alinhamento** (0.11.5): a busca de ângulo (horizontal e CB pull-in/pay-out) só para com |ângulo − alvo| ≤ `statics.angle_tolerance` = 0,005° (antes parava na tolerância da puxada, 0,01 m); a translação para alinhar o MCV com o hub move tudo menos o `Hub Height` (com 2 extremidades, MCV B e hub B andam junto) e repete até o MCV ficar a ≤ `statics.align_tolerance` = 0,001 m do hub. **Passos avaliados** (0.11.6): o passo máximo de cabo (`statics.max_length_step` = 0,5 m) fica dentro da busca: o enquadramento anda de 0,5 em 0,5 m (não dobra além disso), cada passo é uma avaliação de verdade (ângulo/UF lidos) e a primeira troca de sinal já vira o intervalo de refino. A correção do cabo do guindaste na horizontal também fica limitada a 0,5 m por iteração. `statics.max_evaluations` = 100 e `statics.max_height_iterations` = 20. Os dampings ficam sempre nos limites `statics.robust_min_damping_low/high` = 10 a 1e4 (mínimo) e `statics.robust_max_damping_low/high` = 1e2 a 1e4 (máximo); sem degrau possível, a estática não é repetida. No modelo final, iterações, damping e Jacobiano são os do modelo. **Ajuste da distância entre os hubs** (0.10.3): a cópia do ajuste roda sem atrito com o fundo (`model.end_b_tune_without_friction` = sim), porque o atrito da estática comprimia a linha apoiada (−43 kN com tirante de 0,5 kN); o modelo final mantém o atrito.

### Fase 1: inputs

| Comando | O que faz |
|---|---|
| `cvd inputs list` | Lista os casos da Input Sheet |
| `cvd inputs validate [--id ID] [--out rel.xlsx]` | IDs duplicados, ID × Operation Type × pasta, In/Out, inputs obrigatórios ausentes (`N/I`, `Script`, curva externa), padrões usados no lugar de `N/I`, critérios sem limite |
| `cvd inputs show ID` | Valores resolvidos com a fonte de cada um, e os critérios |
| `cvd inputs diff-legacy ID --json ..\__INPUTS.json` | O que mudou entre a planilha e um JSON antigo |
| `cvd snapshot ID --out <pasta> [--legacy-json]` | Grava `case.json` (com hash/data da planilha e da configuração). `--legacy-json` também grava um `__INPUTS.json` no formato antigo, para os scripts `_03` em diante rodarem com os inputs da planilha durante a transição |

Valores especiais da planilha: `N/I`, `-`, `?`, vazio e `Script` = ausente; textos como `Histerese - WSI ... [15]` = referência a curva externa (exige Override com o nome da curva no modelo ou um número).

### Fase 2: núcleo

| Comando | Substitui | O que faz |
|---|---|---|
| `cvd build ID --out <pasta>` | `_01` + preenchimento manual do Checklist | Monta o modelo a partir do modelo base, grava `case.json` e gera `Checklist_<ID>.xlsx` relendo planilha, configuração e os dois `.dat` do disco |
| `cvd checklist ID --model <caso.dat>` | — | Checklist de um modelo já existente (ex.: ajustado à mão) |
| `cvd dynbase ID [--position 01.Horizontal] [--seastates] [--run]` | montagem à mão do modelo dinâmico | `<pasta do caso>/<nome>_dyn.dat` a partir da posição (padrão `01.Horizontal`): MCV `Anchored` na posição em que está, cabo do guindaste apagado, estática conferida; com `--seastates`, gera em seguida os estados de mar em `<caso>/02.Dynamic` (0.12.0). Desde a 0.12.1 o Z do MCV ancorado é exatamente a altura do hub (comprimento do shape do hub) |
| `cvd seastates ID --base-dyn <dyn.dat> --out <pasta> [--regular]` | `_07`, `_10`, `_13` (geração) | `.dat` com o mesmo nome de antes + `manifest.csv` + `batch_list.txt` |
| `cvd post --id ID --sims <pasta> [--out res.xlsx] [--window] [--workers N]` | `_09`, `_12` (+ tabela pivot) | Mesma tabela "Logs" (só ela é substituída), aba Notas e, com `--window`, Status, Janela, UF, Limites e Sugestão |
| `cvd screen fd ID --base-dyn <dyn.dat> --out janela_fd.xlsx [--compare res.xlsx]` | `_17` | Janela prevista pelo domínio da frequência; `cvd screen compare` compara qualquer previsão com as irregulares |
| `cvd window ID --results res.xlsx [--seed prev.xlsx] [--generate <dyn.dat> --gen-out <pasta>]` | `_11`, `_13` | Janela das irregulares e **busca adaptativa** do Hs limite (aba Busca): próximos Hs por (Tp, direção) estimados pelo UF das simulações já rodadas. Sem `--results` (com `--seed` e/ou `--mares`), monta a 1ª rodada. `--method steps` volta à regra antiga |
| `cvd build-legacy --json ..\__INPUTS.json --base ..\00.Base\base_input.dat --out x.dat` | — | Builder alimentado pelo JSON antigo (regressão) |

**Previsão da janela com linhas histeréticas (0.9.1)**: o OrcaFlex 11.6 **não** roda o domínio da frequência com rigidez flexional histerética (erro "hysteretic bend stiffness ... not available for frequency domain analysis", confirmado no GLF27; a help não lista essa restrição). `screen fd` verifica o modelo antes de qualquer análise e para com a orientação. Para rodar, `--bend-stiffness secant --secant-curvature κ` troca a curva de EIx da linha por EI = M(κ)/κ; não há κ conservador para todos os critérios (κ pequeno = rigidez de "stick", κ grande = "slip", e o amortecimento da histerese fica de fora), então parta de κ = 1/MBR e calibre 2 ou 3 valores com `--compare`. Com histerese, a previsão é só **ponto de partida**: a janela sai das irregulares, com a busca adaptativa (`cvd window --seed`).

**Fórmulas no `cvd_config.xlsx` (0.9.1)**: o `config upgrade` grava com o openpyxl, que guarda as fórmulas sem o resultado. Até 0.9.0, as linhas da aba Mares com fórmula eram ignoradas sem aviso (GLF27: 231 estados de mar viraram 1 direção e 1 Hs no `screen fd`). Agora fórmulas simples (referências da mesma aba e + − × ÷) são calculadas na leitura; outras dão erro pedindo para abrir e salvar a planilha no Excel.

**Checklist**: colunas A–K iguais ao `00.Base\Checklist.xlsx` + fonte do input, expressão OrcaFlex e tolerâncias. Status sugerido com tolerância: `OK`, `VERIFY`, `CHECK DEFAULT` (valor igual ao do modelo base → propriedade provavelmente não escrita), `MISSING INPUT`, `MANUAL`. A célula de status tem fórmula, então recalcula se o verificador corrigir um valor. A coluna "Verificado" e o Status final continuam manuais. Um checklist existente nunca é sobrescrito (`_rev1`, `_rev2`...).

**Diferenças intencionais em relação à pipeline antiga** (cada uma tem teste):

| Onde | Antes | Agora |
|---|---|---|
| Declinação do End A | 90° + ângulo do MCV (150° para um MCV de 60°) | **180° − ângulo** (120°, como no modelo base) |
| End B X (`--catenary fixed`, padrão) | Catenária com três erros de fórmula e tração arbitrária de 0,5 kN | Estimativa geométrica (TDP horizontal, altura h = A − OD/2, inclinação 90° − ângulo) como ponto de partida; depois **ajuste pela estática do OrcaFlex** até a tração efetiva no End B = `model.end_b_tension_target` (0,5 kN), numa cópia do modelo sem lingas, cabo do guincho e masterlink, com a estática limitada a `model.end_b_statics_max_iterations` (2000) iterações. No modelo real só o End B X é gravado. Desliga com `model.tune_end_b = N`. `--catenary legacy` reproduz o `_01` inteiro (inclusive a declinação) |
| Wave search | Instantes comparados como texto | Comparação numérica; mesma regra (evento mais tarde) |
| Passo variável | `"True"`/`"False"` como texto (sempre verdadeiro no `_07`) | Booleano de verdade |
| Pós-processamento | Item com erro derrubava o arquivo; outras abas do Excel apagadas | Célula vazia + coluna `_errors`; outras abas preservadas |
| MCV (0.10.0) | CoG, CoV, inércia (100 t·m²) e altura herdados do modelo base | **CoG = CoV = (G − E; 0; F − D)** (aba "MCVs"; sem D/E, os alternativos da aba: D = 0,3 e E = G − 0,3), **altura = B + F** e **inércia de caixa** com a massa no ar: Lx = G + base/2, Ly = base (0,6 m), Lz = B + F. Reproduz o Modelo 2 do GLF27 (0,62 / 1,22 / 0,72 t·m²). O MCV B (2 extremidades) usa as cotas dele (espelhado: x = −(G − E)) |
| Linha (0.10.0) | Densidade do conteúdo 1,025 (conteúdo contado duas vezes: a massa da Input Sheet já é a da linha cheia); etapa 1 da estática Catenary | `line.contents_density` = 0; `model.line_statics_step1` = Quick |
| 2 extremidades (0.10.0) | Seções sob os BRs com o tipo do BR (BR duplicado na ponta A, sem stiffener na ponta B) | Seções sob os BRs com o tipo da linha flexível; stiffener `Bend Restrictor` na ponta A e `Bend Restrictor B` (conexão relativa ao End B) na ponta B |
| Masterlink (0.10.1) | 1ª estática com o masterlink fixo no ápice das lingas (lingas no comprimento) | Fixo a `model.masterlink_height` = 2 m acima do fundo (4,5 m na 0.10.2), com X na média dos pontos das lingas CB1/CB2 na linha + `model.masterlink_x_offset` = 1 m (estática sem a corcova; se ela não convergir, catenária geométrica). Sequência (0.10.4): masterlink fixo, escada de damping (1e3–1e4 → posições → ÷10 até o piso → posições) → masterlink liberado, mesma escada → posições gravadas → damping e Jacobiano de volta aos valores do modelo. `model.masterlink_position = apice` volta ao anterior |
| Hubs e General (0.10.0) | Hubs "Elastic solid"; Jacobian perturbation factor 1e-5 e passo 0,02 s do modelo base | Hubs "Drawing" (`model.hub_shape_type`); Jacobian 0 (`model.jacobian_perturbation_factor`); passo `simulation.time_step` = 0,05 s já no modelo montado |

**Achado do checklist no JSON do GLF-49**: `MassSubmerged` = 1,76 t, mas `MassInAir − 1,025 × Volume` = 1,756 t (status VERIFY). Na planilha geral atual o GLF-49 está consistente (2,002 − 1,025 × 0,2537 = 1,742).

### Fase 3: estática (substitui `_02`–`_06` e `_08`)

```bat
python -m cvdtools statics positions "<ID>" --model <pasta>\<caso>.dat --out <pasta>\04.Positions
python -m cvdtools statics sweep "<ID>" --model <pasta>\<caso>.dat --state free --from 0 --to 16 --step 0.2 --out <pasta>\sweep_free.csv
```

- **Puxada** = encurtamento da 1ª seção do cabo do guincho a partir da **referência**. A referência é o comprimento em que as lingas da corcova começam a tracionar (`statics.pull_tension_threshold`, padrão 0,01 kN), achado automaticamente com o MCV fixo. Substitui o `_02`, que quebra com o modelo base atual.
- **MCV fixo ou livre**: muda só a conexão do objeto do MCV, conforme `statics.mcv_fixed_state` (padrão `Connection=Anchored`) e `statics.mcv_free_state` (padrão `Connection=Free`). Aceita várias propriedades: `Prop=Valor; Prop2=Valor`.
- **Posições** (casos com corcova), cada uma salva como `.sim`:

| Posição | MCV | Regra |
|---|---|---|
| `01.Horizontal` | livre | ângulo do MCV (`statics.angle_variable`, padrão `Rotation 2`) = 0 |
| `02.CB_PullIn` | livre | ângulo = +`statics.cb_angle` (3°) |
| `03.CB_PayOut` | livre | ângulo = −3° |
| `04.Max_Free_Overpull` | livre | maior puxada com UF ≤ `statics.uf_limit`, procurando acima das três anteriores |
| `05.Max_Fixed_Overpull` | fixo | maior puxada com UF ≤ `statics.uf_limit` |

- **UF**: o mesmo do `_03`, valor estático ÷ limite em cada critério estático com limite numérico. Critérios sem limite ficam fora e são listados. Um critério que aponta para objeto inexistente interrompe o cálculo logo no início.
- **Solver**:
  - enquadra a raiz andando com passo `statics.pull_step` (dobrando a cada passo, mas nunca acima de `statics.max_length_step` = 0,5 m, e cada passo é avaliado) até `statics.pull_max`;
  - refina por falsa posição + bisseção até `statics.pull_tolerance` (0,01 m, a mesma tolerância dos scripts antigos);
  - quando a estática não converge, o ponto vira NaN e é contornado;
  - no overpull, devolve sempre o lado com UF ≤ limite.
- **Saídas**:
  - `positions.csv`: referência, puxada, comprimento do cabo, ângulo, UF, critério que governa e status de cada posição;
  - `statics_log.csv`: **todos** os cálculos de estática, inclusive os que não convergiram, com o UF de cada critério.
- **Casos sem corcova** (DCVD de 2ª, CVD de 1ª): as mesmas posições controlando o comprimento suspenso pelo guincho entram com o builder dessas operações (fase 5).
- Configurações antigas: `python -m cvdtools config upgrade` acrescenta os parâmetros `statics.*` na aba Defaults. Sem isso valem os padrões acima.

### Regressão na máquina com OrcaFlex

Depois de capturar o baseline (fase 0), `pytest` também roda `tests\test_regression_phase2.py`:

1. o builder novo, com o mesmo `__INPUTS.json` e `--catenary legacy`, gera **exatamente** o modelo da pipeline antiga;
2. com `--catenary fixed`, as diferenças ficam restritas à linha `Line`;
3. se o baseline tiver tabela de pós-processamento (`--postproc-table`/`--sims-dir`), a extração nova reproduz cada valor.

## Fase 0: base e regressão

O que existe:

| Item | Onde |
|---|---|
| Pacote instalável com o comando `cvd` | `pyproject.toml`, `src/cvdtools/` |
| Comparação com tolerância (modelos, resultados, tabelas) | `src/cvdtools/regression/compare.py` |
| Modelo OrcaFlex → dicionário (via `.yml`) | `src/cvdtools/regression/fingerprint.py` |
| Captura do caso de referência rodando a pipeline antiga numa cópia | `src/cvdtools/regression/capture.py` |
| Testes | `tests/` |

### Se aparecer "cvd não é reconhecido" (PowerShell)

O `pip` instalou o pacote num Python cuja pasta `Scripts` não está no PATH do terminal (comum no PowerShell sem o conda ativado). Duas saídas:

- Abrir o **Anaconda Prompt** (ou rodar `conda activate base` no PowerShell, depois de `conda init powershell`), ou
- Usar sempre o módulo pelo Python, que funciona em qualquer terminal:

```bat
python -m pip install -e ".[dev]"
python -m cvdtools --version
python -m cvdtools baseline capture --code-base .. --out baseline\GLF49_2CVD_no_flut
python -m pytest -rs
```

`-rs` lista os testes pulados e o motivo (ex.: baseline ainda não capturado), para nenhum teste de regressão ficar de fora sem você perceber.

`python -m cvdtools <comando>` é idêntico a `cvd <comando>` em todo este README.

### 1. Instalar (Anaconda Prompt, uma vez)

```bat
cd "C:\Users\ThyagoCapitanio\Desktop\Trabalhos\11.BW Energy\1327.Golfinho\02.Análise\_code_base\new_code"
pip install -e ".[dev]"
cvd --version
```

`OrcFxAPI` precisa estar instalado no mesmo ambiente (`pip install OrcFxAPI` se ainda não estiver).

### 2. Rodar os testes

```bat
pytest
```

Sem baseline capturado, os testes de baseline aparecem como *skipped*. Sem OrcFxAPI, os de OrcaFlex também.

### 3. Capturar o caso de referência (GLF-49, `1327.2CVD_no_flut`)

```bat
cvd baseline capture --code-base .. --out baseline\GLF49_2CVD_no_flut
```

O comando:
1. copia `__INPUTS.json`, `00.Base\base_input.dat`, `_01.GenerateModel.py` e `_02.AdjustWireWinch.py` para uma pasta temporária;
2. roda o `_01` (e o `_02`, que é rodado duas vezes quando o arc length inicial do cabo do guincho não é zero, como o script exige);
3. grava em `baseline\GLF49_2CVD_no_flut`: os modelos base, gerado e ajustado em `.yml`; os resultados estáticos (critérios de aceitação e itens estáticos do pós-processamento); a saída de console dos scripts antigos; e um `manifest.json` com hashes;
4. confere que nenhum arquivo da pasta-mãe mudou.

Opcional, para congelar também um pós-processamento dinâmico já rodado (usado na fase 2):

```bat
cvd baseline capture --code-base .. --out baseline\GLF49_2CVD_no_flut ^
    --postproc-table "<caminho>\results_irreg.xlsx" --sims-dir "<caminho>\03.Dynamic\Iterative_Irreg"
```

Um baseline completo (com `manifest.json`) nunca é sobrescrito. Uma captura interrompida (sem `manifest.json`) é refeita automaticamente na mesma pasta.

Se o `_02.AdjustWireWinch.py` falhar (acontece com o `base_input.dat` atual: cabo do guincho com duas seções e arc length inicial em branco), o baseline é gravado sem o modelo ajustado e o erro fica em `manifest.json` → `adjust_error`. Para pular o `_02` de propósito, use `--no-adjust`.

### 4. Verificar

```bat
cvd baseline verify --dir baseline\GLF49_2CVD_no_flut --code-base ..
pytest
```

`verify` acusa se algum arquivo do baseline foi editado ou se algum script ou input da pipeline antiga mudou desde a captura.
Com o baseline capturado, `pytest` também roda a pipeline antiga de novo e confirma que ela é determinística.

### Comparar dois modelos quaisquer

```bat
cvd compare-models baseline\GLF49_2CVD_no_flut\generated_model.yml outro_modelo.dat --rel-tol 1e-6
```

## Versionamento

Repositório git com `.gitignore` para arquivos de projeto, do OrcaFlex e o baseline (dados de cliente). Veja "Para desenvolver" no início.

## Observações sobre o baseline

- O baseline reflete a pipeline **como ela está**, inclusive os bugs conhecidos (empuxo com `OD*2` no `solveCatenary`). Nas fases seguintes, cada correção de bug vira uma diferença esperada e documentada, nunca silenciosa.
- Os resultados estáticos usam as mesmas chamadas OrcFxAPI do `_03` (critérios) e do `_12` (itens estáticos).
