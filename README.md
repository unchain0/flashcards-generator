# Flashcards Generator

Gera flashcards de cloze para o Anki a partir de PDFs e PPTX usando o NotebookLM. Tudo roda no computador do usuário: o login do Google acontece no navegador uma única vez e a geração é feita pelo CLI, sem servidor.

## Requisitos

- Rust 1.99 (fixado em `rust-toolchain.toml`), com compilador C e CMake
- Chrome ou Chromium instalado
- `qpdf` e `pdftotext` para PDFs; LibreOffice (`soffice`) para PPTX

## Instalação

```bash
cargo install --locked --path rust/cli
flashcards --help
```

## Uso

### 1. Entrar no NotebookLM

```bash
flashcards auth login
```

Abre uma janela do navegador. Entre com a conta Google, e o CLI detecta o redirecionamento, confere a sessão listando os notebooks e grava as credenciais em um diretório privado do usuário. Por padrão, Linux usa `~/.local/share/flashcards-generator/session`; `--data-dir` escolhe outro caminho.

A sessão não é renovada pelo CLI. Quando o NotebookLM recusar as credenciais, rode `flashcards auth login` novamente.

### 2. Gerar os flashcards

```bash
flashcards generate --input-dir ./materiais --output-dir ./output
```

Cada PDF ou PPTX gera um CSV de duas colunas no formato `{{c1::resposta}}`. O comando aceita até dez arquivos por execução.

Opções úteis:

| Opção | Efeito |
| --- | --- |
| `--files aula-01.pdf,aula-02.pptx` | Gera só os arquivos indicados |
| `--include 'aula-*'` / `--exclude '*rascunho*'` | Filtra nomes por glob |
| `--difficulty`, `--quantity`, `--language`, `--instructions` | Ajusta o conteúdo pedido ao NotebookLM |
| `--timeout 900` | Limite por documento, em segundos |

### 3. Combinar CSVs

```bash
flashcards merge --folder ./output --deduplicate
```

### 4. Conferir ou limpar notebooks

```bash
flashcards notebook status
flashcards notebook cleanup --all
```

`cleanup` apaga todos os notebooks visíveis para a conta. Ele nunca pede confirmação, então `--all` é obrigatório.

`--json` em qualquer comando devolve um único objeto no stdout. Erros vão para stderr. Os códigos de saída são `0` para sucesso, `1` para falha operacional e `2` para argumentos inválidos.

### Sessão alternativa

`--storage-file` (ou `FLASHCARDS_NOTEBOOKLM_STORAGE_FILE`) aceita um `storage_state.json` existente do NotebookLM e tem precedência sobre a sessão gravada por `auth login`.

## Segurança

A sessão do NotebookLM fica apenas na máquina local, em diretório `0700` e arquivo `0600`. Nenhum documento, cartão ou credencial sai do computador, e nada é enviado a um servidor do projeto.

## Desenvolvimento

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked
cargo test --workspace --locked
cargo build --release --locked
```

## Limitações

- O NotebookLM não tem API oficial; a integração usa as chamadas da interface web e pode quebrar quando o serviço mudar.
- `auth login` precisa de uma sessão gráfica para o navegador.
- O CLI não renova credenciais sozinho.

## Licença

MIT. Veja [LICENSE](LICENSE).
