# cvdtools: contexto para o Claude Code

Pacote Python que substitui os scripts `_00`–`_18` do pipeline CVD/DCVD (OrcaFlex) da NSG Engenharia.
Projeto de referência: 1349 Golfinho Boost (código 1327, cliente BW Energy). Responsável: Thyago Capitanio.
Repositório: github.com/ThyagoCap/cvdtools (privado). Responda em português.

## Regras permanentes (do usuário)

- **Não modifique os códigos nem a planilha da pasta base** (`_code_base`, um nível acima desta pasta: scripts
  antigos, `00.Base`, `1327.planilha_geral_offline.xlsx`). A planilha geral é só leitura.
- **Nunca sobrescreva** `cvd_config.xlsx` nem `cvd_curves.xlsx` do usuário: parâmetros novos entram por
  `python -m cvdtools config upgrade` (faz `.bak`) ou `--set chave=valor`.
- **Dúvidas ou permissões necessárias: pergunte no começo**, antes de trabalhar.
- **Toda mudança de comportamento:** sobe a versão (`pyproject.toml`, `src/cvdtools/__init__.py`,
  `tests/test_package.py`), atualiza o `README.md` e acrescenta uma linha ao histórico da nota do Obsidian
  "Pipeline CVD+DCVD - passo a passo" (`C:\Users\ThyagoCapitanio\Desktop\NSG-Obsidian\NSG\Ferramentas\Pipeline CVD+DCVD\`).
  Se um comando mudar, corrija também o passo a passo. As notas podem ter sido editadas pelo usuário: leia antes de escrever.
- Parâmetro novo: em `src/cvdtools/params.py` (com padrão e descrição), nunca valor fixo no código.
- Mensagens, logs e docstrings em português.

## Estrutura

- `params.py`: registro de todos os parâmetros (Input Sheet, Defaults, Overrides). Origem de um valor: Overrides > Input Sheet > Defaults.
- `io/` (planilha geral, `cvd_config.xlsx`, snapshot `case.json`), `inputs.py`, `schema.py`, `validation.py`.
- `model/`: `builder.py` (monta o modelo a partir do `00.Base/base_input.dat`), `camelback.py` (corcova, masterlink),
  `twoends.py` + `endb.py` (2ª extremidade: MCV B, ajuste da distância entre hubs, estática final em 2 etapas),
  `mcvprops.py` (CoG/CoV/inércia de caixa), `checklist.py`, `view.py` (vista padrão; translação do modelo), `diagnose.py`.
- `statics/`: `engine.py` (avaliação de uma puxada, estados do MCV fixo/livre, cabo do guindaste, alinhamento),
  `solver.py` (busca de raiz robusta), `positions.py` (01.Horizontal, 02/03.CB, 04/05 overpulls).
- `_orcaflex.py`: `calculate_statics` (progresso, Ctrl+C, parada sem progresso, erro máximo) e
  `calculate_statics_robust` (escada de damping).
- `dynamics/`: `dynbase.py` (`_dyn.dat` a partir da 01.Horizontal), `seastates.py` (estados de mar da aba Mares), `run.py`.
- `post/` (extração, janela, busca adaptativa), `screening/` (domínio da frequência, semi-dinâmico, resposta da embarcação).
- `cli.py` (`python -m cvdtools ...`), `api.py` (uma função por etapa, usada pelos scripts),
  `setup_project.py` (`setup`, `update`), `base_repo.py` (modelo base baixado do repositório `base_repo` do `cvd_project.json` para `.cvd_base`; 0.14.0) e `templates/scripts/` (scripts da equipe: `00_caso.py`, `01_...` a `99_...`).

## Decisões de modelagem e solver (com o porquê)

- **Escada de damping** (`calculate_statics_robust`): começa no nível mais baixo que convergiu na estática anterior (uma escada só no `StaticsEngine`: masterlink fixo e livre e todos os passos de cabo; 0.13.1)
  (`DampingLadder`); convergiu → grava posições e desce ÷10 até o piso (mín. 10–1e4, máx. 1e2–1e4); não convergiu
  (ou 120 s sem o erro cair à metade) → resíduo < `statics.low_residual` (0,01): Jacobiano ×10 (0 → 1e-3 → 1e-2 → 0,1)
  com o mesmo damping; resíduo alto: damping ×10 e Jacobiano ≥ 1e-3. "Latest error" > `statics.max_error` (1e5)
  cancela a tentativa. O modelo salvo volta sempre a damping/iterações/Jacobiano originais.
  `StaticsMaxIterations` precisa ser int.
- **Busca de puxada**: enquadramento com passo de no máximo `statics.max_length_step` (0,5 m), cada passo avaliado;
  troca de sinal no caminho vira o intervalo de refino (`EarlyBracket`); refino Illinois + bisseção até 0,01 m,
  ou até |Δângulo| ≤ `statics.angle_tolerance` (0,005°) nos alvos de ângulo. `statics.max_evaluations` = 100.
- **Masterlink**: fixo só na 1ª estática do `statics positions`; depois que uma estática com ele livre converge,
  o cabo muda só com ele livre. Posição inicial "baixo": `model.masterlink_height` = 2 m acima do fundo,
  X = média das lingas + 1 m.
- **Horizontal**: alterna ângulo 0 (guincho) e altura do hub (guindaste, correção ≤ 0,5 m por iteração); depois
  translada tudo menos o `Hub Height` até o MCV ficar a ≤ 1 mm do hub em X (com 2 extremidades, MCV B e hub B andam junto).
- **2 extremidades**: MCV B livre em X (constraint) com tirante de 0,5 kN para achar a distância entre hubs;
  a cópia desse ajuste roda sem atrito com o fundo.
- **Modelo dinâmico** (`dynbase`): MCV `Anchored` com Z = altura do hub (comprimento do shape do hub = A − F),
  cabo do guindaste apagado; movimento da embarcação vem do modelo base sem mudança.
- Linhas com curva de histerese: o OrcaFlex não roda domínio da frequência com elas (usar rigidez secante na triagem).

## Testes

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q -m "not orcaflex and not baseline"
```

Os testes usam um OrcFxAPI falso (`tests/fake_orcfxapi.py`). Os marcados `orcaflex`/`baseline` precisam do
OrcaFlex e do baseline local. Precisam passar em Python 3.9 (sem sintaxe `X | Y` fora de anotações com
`from __future__ import annotations`).

## Uso pela equipe

Pasta de projeto separada (sem esta pasta de código): `pip install "cvdtools[orcaflex] @ git+https://github.com/ThyagoCap/cvdtools.git"`,
`python -m cvdtools setup --config-from <pasta de referência>`, scripts em `scripts/` rodados no VS Code;
atualização com `python -m cvdtools update`. Nesta pasta (desenvolvimento) a instalação é `pip install -e .`.

## Arquivos que não entram no git

`cvd_project.json`, `cvd_config*.xlsx`, `cvd_curves.xlsx`, `casos/`, `scripts/`, `baseline/`, `.vscode/`, `.dat`, `.sim`, logs (ver `.gitignore`).
