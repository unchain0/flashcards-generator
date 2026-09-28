# Flashcards Generator

Gera flashcards Anki em formato Cloze Deletion a partir de PDFs e PPTX usando o NotebookLM do Google.

## Características

- **Formato Cloze Deletion 100%**: Todos os flashcards usam o formato `{{c1::resposta}}` para estudo ativo
- **Suporte a PDFs grandes**: Divide automaticamente PDFs com mais de 50 páginas em chunks otimizados
- **Filtragem de qualidade**: Remove automaticamente flashcards triviais, duplicados e com baixo valor educacional
- **Detecção de capítulos**: Identifica capítulos no PDF e filtra seções irrelevantes (Copyright, Índice, etc.)
- **Suporte a PPTX**: Converte apresentações PowerPoint para flashcards

## Instalação

```bash
# Clone o repositório
git clone <repo-url>
cd flashcards-generator

# Instale a aplicação no ambiente Python 3.14.7
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
Em produção, configure `FLASHCARDS_COMPANION_WEB_ORIGIN` com a origem HTTPS
exata da aplicação. O navegador pode pedir autorização para a conexão local.
Não execute `notebooklm login` no servidor web.

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
