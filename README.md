# Flashcards Generator

Gera flashcards Anki em formato Cloze Deletion a partir de PDFs e PPTX usando o NotebookLM do Google.

## Características

- **Formato Cloze Deletion 100%**: Todos os flashcards usam o formato `{{c1::resposta}}` para estudo ativo
- **Suporte a PDFs grandes**: Divide automaticamente PDFs com mais de 50 páginas em chunks otimizados
- **Filtragem de qualidade**: Remove automaticamente flashcards triviais, duplicados e com baixo valor educacional
- **Detecção de capítulos**: Identifica capítulos no PDF e filtra seções irrelevantes (Copyright, Índice, etc.)
- **Suporte a PPTX**: Converte apresentações PowerPoint para flashcards

## Monitoramento

O frontend compilado para produção e os executáveis da migração Rust usam
Sentry por padrão, nos projetos `flashcards-frontend`, `flashcards-web` e
`flashcards-companion` da organização `unchain0`. A publicação Rust foi
autorizada em 2026-10-05 com as pendências de cobertura informadas. O Compose
seleciona `Dockerfile.rust`; o gate continua exigindo 100%.

Os eventos mantêm apenas metadados de erro e posições de código. Senhas,
identidade, documentos, cartões, anexos, cookies e perfis Google são removidos
antes do envio. Replay, rastreamento de requisições e captura de console não
estão habilitados; o Sentry também está configurado para não armazenar IPs.

Nos executáveis Rust, `FLASHCARDS_SENTRY_ENABLED=false` desativa o envio.
`SENTRY_ENVIRONMENT` aceita `development`, `production`, `staging` ou
`sentry-validation`. Os DSNs podem ser substituídos com
`FLASHCARDS_WEB_SENTRY_DSN` e `FLASHCARDS_COMPANION_SENTRY_DSN`; `SENTRY_DSN`
serve como alternativa comum. O frontend não envia eventos durante desenvolvimento.

Para validar a ingestão com um evento sintético:

```bash
SENTRY_ENVIRONMENT=sentry-validation cargo run --locked -p flashcards-delivery --example sentry_smoke -- web
SENTRY_ENVIRONMENT=sentry-validation cargo run --locked -p flashcards-delivery --example sentry_smoke -- companion
```

## Instalação

```bash
# Clone o repositório
git clone <repo-url>
cd flashcards-generator

# Instale a aplicação no ambiente Python 3.14.8
uv sync --all-extras --dev

# Instale e compile a interface web Vite+
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..

# Instale o Chromium do Playwright
uv run playwright install chromium

```

## Uso

O painel web Litestar serve a interface Vite+ compilada no mesmo domínio:

```bash
uv run flashcards-web
```

`uv run python -m flashcards_generator` e `uv run python main.py` também iniciam
esse mesmo servidor web.

Abra `http://127.0.0.1:8000` e entre com a senha provisionada. O auxiliar
NotebookLM precisa rodar no computador do usuário. Em outro terminal, informe
a origem exata da aplicação e inicie o auxiliar:

```bash
FLASHCARDS_COMPANION_WEB_ORIGIN=http://127.0.0.1:8000 \
  uv run flashcards-companion
```

Na tela web, clique em **Conectar NotebookLM** para conferir a sessão ou fazer
login. O auxiliar mantém um perfil local separado por usuário. Ele recebe uma
capacidade temporária vinculada à sessão web; não recebe o cookie da aplicação.
O Companion escuta em `http://127.0.0.1:8766`; a porta `8765` fica disponível
para o AnkiConnect. Atualize o frontend e o Companion juntos ao adotar essa porta.
Em produção, configure `FLASHCARDS_COMPANION_WEB_ORIGIN` com a origem HTTPS
exata da aplicação. O navegador pode pedir autorização para a conexão local.
Não execute `notebooklm login` no servidor web.

### Companion Rust durante a migração

O Companion Rust pode ser instalado com Cargo a partir deste checkout:

```bash
cargo install --locked --path rust/delivery --bin flashcards-companion
FLASHCARDS_COMPANION_WEB_ORIGIN=https://flashcards.unchain0.com flashcards-companion
```

A compilação usa o Rust fixado em `rust-toolchain.toml` e requer um compilador C
e CMake. O executável funciona sem Python e precisa de Chrome ou Chromium e
qpdf no computador do usuário. PPTX também requer LibreOffice (`soffice` no
`PATH`). Informe a origem exata do seu servidor em
`FLASHCARDS_COMPANION_WEB_ORIGIN`; o Companion escuta somente em
`127.0.0.1:8766`. O Cargo instala o comando em `~/.cargo/bin` por padrão.
O aplicativo guarda seus perfis locais na pasta de dados do usuário. Use
`FLASHCARDS_COMPANION_DATA_DIR` com um caminho absoluto para escolher outra
pasta, ou `FLASHCARDS_COMPANION_BROWSER` para indicar o executável do navegador.
Essa instalação usa o Companion da versão Rust. A aceitação completa da
migração, incluindo geração nativa real, continua pendente.

O fluxo pelo navegador está completo: os arquivos seguem diretamente para o
companion local, que executa chunking, retomada, retries, filtragem de qualidade
e exportação compatível com o Anki. Os documentos e a sessão Google não passam
pelo servidor web.

O acesso usa senha, cookie de sessão `HttpOnly` e Argon2id. Para provisionar
outros acessos localmente, use
`uv run python -m flashcards_generator.delivery.web.user_admin create`; o
comando solicita a senha duas vezes e não coleta nome ou e-mail.

O login Google/NotebookLM ocorre no auxiliar local, usando o perfil do usuário
naquele computador. Não configure a sessão NotebookLM no servidor.

Para testar a autenticação da API, autentique e reutilize o cookie de sessão:

```bash
curl -c /tmp/flashcards-cookies \
  -H 'Content-Type: application/json' \
  -d '{"password":"SUA_SENHA"}' \
  http://127.0.0.1:8000/api/v1/auth/login
```

### Produção

O perfil de produção usa PostgreSQL, migrações Alembic, senhas individuais e
segredos persistentes de sessão/lookup. Configure valores próprios com pelo
menos 32 caracteres para `FLASHCARDS_SESSION_SECRET` e
`FLASHCARDS_AUTH_LOOKUP_SECRET`.

```bash
cp .env.example .env
docker compose up --build
```

O serviço publica em `127.0.0.1:8000` por padrão, aplica `alembic upgrade head`
antes de iniciar e persiste no PostgreSQL e no volume `flashcards-app-data`.
Para cadastrar acessos após a inicialização, execute
`docker compose exec app python -m flashcards_generator.delivery.web.user_admin create`.
O proxy reverso deve fornecer
HTTPS; a aplicação marca o cookie de sessão como `Secure` em produção.

Para uma migração local explícita:

```bash
FLASHCARDS_DATABASE_URL='sqlite+aiosqlite:///./flashcards.db' \
  uv run alembic upgrade head
```

### Verificadores da migração Rust

Rust 1.99.0 fixa compilação, rustfmt e Clippy. Os membros do workspace herdam
os lints: nenhum warning sem justificativa local, no máximo três níveis de
blocos, 80 linhas por função e sete argumentos. A referência Python mantém
Radon; esses limites Rust não são uma equivalência matemática de Radon A.

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked
cargo metadata --format-version 1 --locked --all-features > target/quality/metadata.json
cargo run --locked -p xtask -- check-masa target/quality/metadata.json
bash scripts/rust-check.sh
```

Crie `target/quality` antes de salvar a metadata. O `xtask` verifica identidades
de pacotes, dependências próprias normais, opcionais, de build e de testes,
classificação das crates e herança dos lints. A classificação combina a camada
MASA com a localização: servidor, local ou compartilhada. `delivery-server` e
`integrations-server` não podem alcançar as crates locais de documentos e perfis
Google, nem por dependências de testes ou build. As crates `*-shared` também
não podem adquirir essas capacidades. O checker restringe as dependências
externas diretas dessas crates; metadata não demonstra ausência de I/O em engines.

Instale `cargo-llvm-cov` 0.9.1 e `cargo-deny` 0.20.2 com `cargo install --locked`.
A cobertura usa a nightly datada em `ci/coverage-toolchain`, com seu próprio
`llvm-tools-preview`, separadamente da toolchain de produção:

```bash
rustup toolchain install "$(cat ci/coverage-toolchain)" --profile minimal --component llvm-tools-preview
bash scripts/rust-coverage.sh
```

O script combina testes Cargo e Playwright executando os binários instrumentados.
Para incluir geração real com os PDFs/PPTX sintéticos, passe um arquivo local
de sessão Google válido como único argumento de `scripts/rust-coverage.sh`.
Esse modo executa o Companion instrumentado já compilado, preserva os notebooks
existentes e verifica a remoção dos notebooks de QA. A sessão permanece local.
Um segundo argumento opcional de `rust-coverage.sh` identifica o perfil Chrome
dedicado, fechado. No PDF, o teste prepara uma cópia privada limitada desse
perfil e exige a autenticação pela rota nativa do Companion antes da geração.
Isso verifica captura e persistência de uma sessão existente; não demonstra
entrada de novas credenciais Google. O PPTX reutiliza a sessão local armazenada.
Os testes usam uma pasta privada e descartável com nome curto no cache do
usuário, evitando quotas de `/tmp` e o limite dos sockets Unix do Chromium.
O checker exige contadores inteiros completos de linhas, funções e branches
por fonte de produção, incluindo fontes não importadas, e rejeita relatórios
incompletos ou desconhecidos. Testes e o próprio xtask são ferramentas de
desenvolvimento; entrypoints e adaptadores continuam no inventário de produção.
Antes da coleta, o script registra hashes SHA-256 das fontes de produção,
manifests, lockfile, toolchains, migrações SQL, lista de stop words e configuração
da coleta. Ao finalizar, ele
verifica esses hashes e vincula os dois relatórios ao snapshot. O comando
`xtask check-coverage <metadata> <coverage> <probe> <snapshot>` exige essa
evidência: fontes, configuração, metadata ou relatórios alterados reprovam o gate.
Isso verifica a consistência da coleta local; não é uma assinatura de build.
Arquivos com nomes de testes só saem do inventário quando o módulo proprietário
os declara com `#[cfg(test)]`; o nome do arquivo sozinho não exclui código.
Uma suíte separada homologa condições, padrões, laços, macros e código assíncrono.
Na nightly selecionada, `match`, `?`, `for` e caminhos gerados por `.await`
não demonstraram instrumentação completa.
O gate falha nessa condição: 100% dos branches instrumentados e LLVM regions
não comprovam 100% de todos os branches de produção.

```bash
docker build -f Dockerfile.rust -t flashcards-generator:rust-qa .
image_id="$(docker image inspect --format '{{.Id}}' flashcards-generator:rust-qa)"
bash scripts/rust-security.sh "$image_id"
```

O gate de segurança exige uma imagem imutável. cargo-deny verifica advisories
e fontes Rust; Trivy verifica o lockfile pnpm incluindo desenvolvimento,
segredos do checkout e vulnerabilidades/segredos da imagem, em todas as
severidades, inclusive sem correção disponível. Os relatórios locais em
`target/quality` são privados; o relatório de segredos não é publicado como
artefato de CI. Um binário Rust comum não oferece ao Trivy um inventário
independente das suas crates: a auditoria de Cargo.lock deve acompanhar o build.

### Validação da imagem Rust

Para validar a imagem do servidor Rust durante a migração:

```bash
docker build -f Dockerfile.rust -t flashcards-generator:rust-qa .
bash scripts/rust-container-check.sh
```

Essa imagem contém o executável Rust e o frontend compilado. A validação usa
PostgreSQL descartável e verifica migrações, login, cookies, reinício e logout.
O Dockerfile padrão continua sendo a referência Python. O Compose de produção
seleciona `Dockerfile.rust`, conforme a autorização de publicação de 2026-10-05.
Os volumes PostgreSQL e as chaves de autenticação existentes são preservados;
o deploy não cria outra senha de bootstrap.

Na coleta de 2026-10-05, passaram 306 testes Rust e cinco contratos de navegador
com Companion simulado. A cobertura de produção foi 6614/6726 linhas,
907/916 funções e 1172/1204 resultados de branches instrumentados. O gate
permanece reprovado; as sondas de `match`, `?`, `.await` e `for` também não
demonstram instrumentação completa. A autorização de publicação não altera
esses critérios nem comprova geração nativa real no NotebookLM.

Para verificar upload e geração reais pelo Companion Rust no navegador:

```bash
cargo run --locked -p flashcards-delivery --example notebooklm_companion_smoke -- <storage-file>
cargo run --locked -p flashcards-delivery --example notebooklm_companion_smoke -- <storage-file> pptx
```

Use um arquivo local `storage_state.json` com sessão válida do NotebookLM.
Esse teste exige Docker, Chromium do Playwright, qpdf e pdftotext. Ele usa
PostgreSQL descartável, copia a sessão para uma pasta privada temporária e gera
um PDF sintético de biologia celular ou usa a apresentação sintética do teste
de conversão, quando informado `pptx`. PPTX requer LibreOffice. O navegador
envia o documento ao Companion,
executado em modo `release`, confere a conclusão e baixa o CSV. Ao terminar,
o teste verifica a remoção do
notebook de QA e a preservação dos notebooks anteriores. As capturas ficam em
`frontend/test-results/live-companion/`. Esse teste usa a sessão existente;
não valida um novo login interativo do Google nem importa notas no Anki.

Para conferir a captura da sessão pelo login nativo, use um perfil dedicado
do NotebookLM com o navegador fechado:

```bash
cargo run --locked -p flashcards-delivery --example notebooklm_browser_smoke -- <storage-file> <browser-profile> [session-output] [browser-profile-output]
```

O teste usa uma cópia temporária privada e verifica que os arquivos do perfil
original não mudaram. Quando a sessão inicial é válida, também compara os IDs
dos notebooks. Sem essa sessão, a preservação do inventário fica não demonstrada.
Ele não envia documentos.
Uma sessão copiada expirada pode exigir login interativo; reutilizar uma sessão
não comprova a entrada de novas credenciais Google.
O argumento opcional `session-output` salva a sessão verificada em um novo arquivo
local para os próximos testes. Seu diretório deve existir, ser absoluto e privado;
o teste publica o arquivo atomicamente e não substitui arquivos existentes.
O último argumento opcional conserva uma cópia do perfil autenticado em um
diretório absoluto, privado e vazio, separado da origem, para QA posterior.
A cópia rejeita perfis ativos, links e entradas especiais, limita o inventário
a 10.000 entradas e 512 MiB, e mantém os arquivos originais intactos.
Para testar autenticação nativa junto com geração e cobertura, use:

```bash
bash scripts/rust-coverage.sh <storage-file> <browser-profile>
```

## Fluxo pelo navegador

O formulário fica desativado até o companion local confirmar uma sessão válida
do NotebookLM. Depois da conexão, o navegador envia PDFs e PPTX diretamente ao
companion, acompanha o job local e oferece os CSVs gerados para download.

## Importação no Anki

Os arquivos CSV gerados podem ser importados manualmente no Anki. O projeto
mantém um adaptador AnkiConnect no backend, mas a importação direta ainda não é
uma ação disponível na interface web.

### Importação manual via CSV

1. Abra o Anki
2. Arquivo → Importar → Selecione o arquivo `.csv` gerado
3. Configure:
   - **Tipo de nota:** Cloze
   - **Delimitador:** Vírgula
   - **Campos:** Frente (coluna 1), Verso (coluna 2)
4. Clique em **Importar**

## Como Funciona

### Chunking de PDFs Grandes

PDFs com mais de 50 páginas são automaticamente divididos em chunks de até 30 páginas:

- **Detecção de capítulos**: Se o PDF tiver bookmarks/outline, os chunks respeitam os limites dos capítulos
- **Filtragem inteligente**: Seções como Copyright, Índice, Prefácio e Índice remissivo são ignoradas
- **Sem overlap**: Quando chunking por capítulos é usado, não há páginas duplicadas entre chunks

### Qualidade dos Flashcards

O sistema aplica filtros automáticos para garantir qualidade:

- Remove cards com apenas palavras triviais (artigos, preposições)
- Remove cards com respostas muito curtas (menos de 2 palavras)
- Remove cards com linguagem subjetiva ("bom", "ruim", "importante")
- Remove cards duplicados ou muito similares (similaridade > 85%)

### Formato Cloze Deletion

Todos os flashcards seguem o formato:

```
Frente: O {{c1::SQLAlchemy}} é um ORM para Python.
Verso: Object-Relational Mapping facilita a interação com bancos de dados.
```

Dicas para melhores resultados:
- Cada card testa **apenas um conceito**
- Contexto é sempre incluído na frente
- Listas usam clozes progressivos: `{{c1::itemA}} {{c2::itemB}}`

## Limitações

- Requer conexão com internet (usa API do NotebookLM)
- PDFs muito grandes podem demorar vários minutos
- Qualidade depende da clareza do texto no PDF
- Imagens e diagramas não são processados (apenas texto)

## Solução de Problemas

### NotebookLM ainda não conecta

A interface web não executa o comando NotebookLM no servidor. Verifique se o
auxiliar está aberto no computador e se `FLASHCARDS_COMPANION_WEB_ORIGIN`
corresponde exatamente à origem da aplicação. Não use `notebooklm login` no
servidor, pois isso armazenaria a sessão Google fora do computador do usuário.

### Flashcards duplicados

O sistema já remove duplicatas automaticamente. Se ainda encontrar duplicados:

```bash
# Use o modo deduplicate ao importar no Anki
# Ou remova manualmente após a importação
```

## Desenvolvimento

```bash
# Instalar a toolchain e dependências do navegador
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend exec playwright install chromium
uv sync --all-extras --dev

# Compilar a interface antes de iniciar/testar o backend
pnpm --dir frontend run build

# Executar Vite+ em um terminal e Litestar em outro
pnpm --dir frontend run dev
uv run flashcards-web

# Gates do frontend
pnpm --dir frontend run check
pnpm --dir frontend run test
pnpm --dir frontend run e2e

# Executar testes backend
uv run pytest

# Executar testes com cobertura
uv run pytest --cov=flashcards_generator --cov-report=term-missing

# Linting
uv run ruff check .
uv run ruff format .
uv run ty check src/flashcards_generator
```

## Arquitetura

O projeto segue a arquitetura MASA em cinco áreas:

- **domain_models/**: entidades, exceções e objetos de valor puros;
- **engines/**: transformações determinísticas de cloze, matemática e qualidade;
- **services/**: casos de uso, DTOs, portas e orquestração;
- **integrations/**: banco, filesystem, PDF/PPTX, NotebookLM e AnkiConnect;
- **delivery/**: composição concreta, servidor Litestar e companion local.

As dependências apontam para dentro: `services` usa portas próprias, enquanto
`delivery` conecta implementações de `integrations` às regras de negócio.

O ambiente de desenvolvimento e CI usa a stack Astral: `uv` gerencia o
ambiente e o lockfile, `ruff` cuida de lint/format e `ty` executa a checagem de
tipos.

## Licença

[MIT License](LICENSE)
