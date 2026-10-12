use clap::{Args, Parser, Subcommand};
use flashcards_integrations::{
    csv_merge::LocalCsvMerger, deck_exporter::CsvDeckExporter,
    document_preparation::LocalDocumentPreparer, notebooklm::NotebookLMClient,
};
use flashcards_services::{
    csv_merge::{CsvMerger, MergeCsvRequest},
    document_export::{DocumentExportResult, generate_source_csv},
    document_inputs::safe_filenames,
    generation_options::GenerationOptions,
    notebooklm::NotebookLMGateway,
};
use serde_json::{Value, json};
use std::{
    error::Error,
    fs,
    path::{Path, PathBuf},
    process::ExitCode,
};

const EXIT_USAGE: u8 = 2;
const EXIT_FAILURE: u8 = 1;

#[derive(Parser)]
#[command(name = "flashcards", about = "Generate, manage, and merge flashcards")]
struct Cli {
    /// Emit exactly one JSON result on stdout.
    #[arg(long, global = true)]
    json: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Generate CSV flashcards from PDF and PPTX files using local `NotebookLM` credentials.
    Generate(GenerateArgs),
    /// Combine generated CSV files.
    Merge(MergeArgs),
    /// Inspect or delete `NotebookLM` notebooks using local credentials.
    Notebook(NotebookArgs),
}

#[derive(Args)]
struct MergeArgs {
    #[arg(short, long)]
    folder: PathBuf,
    #[arg(short, long, default_value = "merged_flashcards.csv")]
    output: String,
    #[arg(short, long)]
    deduplicate: bool,
    #[arg(long)]
    no_recursive: bool,
}

#[derive(Args)]
struct GenerateArgs {
    /// Directory containing PDF or PPTX source files.
    #[arg(short, long)]
    input_dir: PathBuf,
    #[arg(short, long, default_value = "output")]
    output_dir: PathBuf,
    #[arg(short, long, default_value = "medium")]
    difficulty: String,
    #[arg(short, long, default_value = "standard")]
    quantity: String,
    #[arg(short, long, default_value = "pt_BR")]
    language: String,
    #[arg(long, default_value_t = 900)]
    timeout: u32,
    #[arg(long, default_value = "")]
    instructions: String,
    #[arg(long)]
    single_cloze: bool,
    /// Include only source names matching this glob.
    #[arg(long)]
    include: Option<String>,
    /// Exclude source names matching this glob.
    #[arg(long)]
    exclude: Option<String>,
    /// Comma-separated source names relative to --input-dir.
    #[arg(long)]
    files: Option<String>,
    /// `NotebookLM` Playwright storage-state file. Defaults to `FLASHCARDS_NOTEBOOKLM_STORAGE_FILE`.
    #[arg(long, env = "FLASHCARDS_NOTEBOOKLM_STORAGE_FILE")]
    storage_file: PathBuf,
}

#[derive(Args)]
struct NotebookArgs {
    #[command(subcommand)]
    command: NotebookCommand,
}

#[derive(Subcommand)]
enum NotebookCommand {
    /// Check credentials and list notebooks.
    Status(StorageArgs),
    /// List notebooks without changing them.
    List(StorageArgs),
    /// Delete all notebooks. This never prompts; --all is required.
    Cleanup(CleanupArgs),
}

#[derive(Args)]
struct StorageArgs {
    /// `NotebookLM` Playwright storage-state file. Defaults to `FLASHCARDS_NOTEBOOKLM_STORAGE_FILE`.
    #[arg(long, env = "FLASHCARDS_NOTEBOOKLM_STORAGE_FILE")]
    storage_file: PathBuf,
}

#[derive(Args)]
struct CleanupArgs {
    #[command(flatten)]
    storage: StorageArgs,
    /// Confirm deletion of every notebook visible to these credentials.
    #[arg(long)]
    all: bool,
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(cli).await {
        Ok(result) => {
            print_result(result.0, &result.1);
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("flashcards: {error}");
            ExitCode::from(error.code)
        }
    }
}

struct CommandResult(bool, Value);

async fn run(cli: Cli) -> Result<CommandResult, CliError> {
    let json_output = cli.json;
    let value = match cli.command {
        Command::Merge(args) => merge(args)?,
        Command::Generate(args) => generate(args).await?,
        Command::Notebook(args) => notebook(args).await?,
    };
    Ok(CommandResult(json_output, value))
}

fn merge(args: MergeArgs) -> Result<Value, CliError> {
    let request = MergeCsvRequest::new(
        args.folder,
        args.output,
        !args.no_recursive,
        args.deduplicate,
    )
    .map_err(|error| CliError::usage(error.to_string()))?;
    let details = LocalCsvMerger
        .merge(&request)
        .map_err(|error| CliError::failure(error.to_string()))?;
    Ok(json!({
        "command": "merge",
        "rows_before": details.rows_before,
        "rows_written": details.rows_written,
        "duplicates_removed": details.duplicates_removed,
    }))
}

async fn generate(mut args: GenerateArgs) -> Result<Value, CliError> {
    args.input_dir = args
        .input_dir
        .clone()
        .canonicalize()
        .unwrap_or_else(|_| args.input_dir.clone());
    let options = generation_options(&args)?;
    fs::create_dir_all(&args.output_dir)
        .map_err(|error| CliError::failure(format!("cannot create output directory: {error}")))?;
    let paths = source_paths(&args)?;
    let names = paths
        .iter()
        .map(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .map(str::to_owned)
        })
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| CliError::usage("source names must be valid UTF-8"))?;
    let filenames = safe_filenames(&names).map_err(|error| CliError::usage(error.to_string()))?;
    let client = NotebookLMClient::from_storage_file(&args.storage_file)
        .await
        .map_err(|error| CliError::failure(error.to_string()))?;
    let preparer = LocalDocumentPreparer::default();
    let exporter = CsvDeckExporter;
    let mut documents = Vec::new();
    for (source, filename) in paths.iter().zip(&filenames) {
        let result = generate_source_csv(
            &preparer,
            &client,
            &exporter,
            source,
            &args.output_dir,
            filename,
            &options,
        )
        .await;
        documents.push(document_value(&result));
    }
    let failed = documents.iter().any(|item| item["outcome"] == "failed");
    if failed {
        let _ = client.finish_cleanup().await;
        return Err(CliError::failure(
            "one or more documents could not be generated",
        ));
    }
    if !client.finish_cleanup().await {
        return Err(CliError::failure("NotebookLM deferred cleanup failed"));
    }
    Ok(json!({"command": "generate", "documents": documents}))
}

fn generation_options(args: &GenerateArgs) -> Result<GenerationOptions, CliError> {
    GenerationOptions::from_form(&[
        ("difficulty".into(), args.difficulty.clone()),
        ("quantity".into(), args.quantity.clone()),
        ("language".into(), args.language.clone()),
        ("timeout".into(), args.timeout.to_string()),
        ("instructions".into(), args.instructions.clone()),
        ("single_cloze".into(), args.single_cloze.to_string()),
    ])
    .map_err(|error| CliError::usage(error.to_string()))
}

fn validate_glob(pattern: &str) -> Result<glob::Pattern, CliError> {
    glob::Pattern::new(pattern)
        .map_err(|error| CliError::usage(format!("invalid glob pattern '{pattern}': {error}")))
}

fn source_paths(args: &GenerateArgs) -> Result<Vec<PathBuf>, CliError> {
    if !args.input_dir.is_dir() {
        return Err(CliError::usage("--input-dir must be an existing directory"));
    }
    let include_pattern = args
        .include
        .as_ref()
        .map(|p| validate_glob(p))
        .transpose()?;
    let exclude_pattern = args
        .exclude
        .as_ref()
        .map(|p| validate_glob(p))
        .transpose()?;
    let requested = args.files.as_deref().map(parse_files).transpose()?;
    let mut paths: Vec<PathBuf> = if let Some(files) = requested {
        files
            .into_iter()
            .map(|file| args.input_dir.join(file))
            .collect()
    } else {
        fs::read_dir(&args.input_dir)
            .map_err(|error| CliError::failure(error.to_string()))?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .collect()
    };
    paths.sort();
    paths.retain(|path| source_matches(path, include_pattern.as_ref(), exclude_pattern.as_ref()));
    if paths.is_empty() {
        return Err(CliError::usage("no PDF or PPTX source files matched"));
    }
    if paths.len() > 10 {
        return Err(CliError::usage(
            "at most 10 source files may be generated at once",
        ));
    }
    Ok(paths)
}

fn parse_files(raw: &str) -> Result<Vec<&str>, CliError> {
    let files = raw
        .split(',')
        .map(str::trim)
        .filter(|file| !file.is_empty())
        .collect::<Vec<_>>();
    if files.is_empty()
        || files
            .iter()
            .any(|file| Path::new(file).is_absolute() || file.contains(".."))
    {
        return Err(CliError::usage(
            "--files must contain relative source names",
        ));
    }
    Ok(files)
}

fn source_matches(
    path: &Path,
    include: Option<&glob::Pattern>,
    exclude: Option<&glob::Pattern>,
) -> bool {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    let extension = path.extension().and_then(|value| value.to_str());
    path.is_file()
        && matches!(
            extension.map(str::to_ascii_lowercase).as_deref(),
            Some("pdf" | "pptx")
        )
        && include.is_none_or(|pattern| pattern.matches(name))
        && exclude.is_none_or(|pattern| !pattern.matches(name))
}

fn document_value<P: Error, G: Error, X: Error>(result: &DocumentExportResult<P, G, X>) -> Value {
    json!({
        "outcome": match result.outcome {
            flashcards_services::local_jobs::SourceOutcome::Completed => "completed",
            flashcards_services::local_jobs::SourceOutcome::Failed => "failed",
            flashcards_services::local_jobs::SourceOutcome::Skipped => "skipped",
        },
        "artifacts": result.artifacts,
        "issues": result.issues.iter().map(ToString::to_string).collect::<Vec<_>>(),
    })
}

async fn notebook(args: NotebookArgs) -> Result<Value, CliError> {
    match args.command {
        NotebookCommand::Status(args) => list_notebooks(args.storage_file, "status").await,
        NotebookCommand::List(args) => list_notebooks(args.storage_file, "list").await,
        NotebookCommand::Cleanup(args) => cleanup(args).await,
    }
}

async fn list_notebooks(storage_file: PathBuf, command: &str) -> Result<Value, CliError> {
    let client = NotebookLMClient::from_storage_file(&storage_file)
        .await
        .map_err(|error| CliError::failure(error.to_string()))?;
    let notebooks = client
        .list_notebooks()
        .await
        .map_err(|error| CliError::failure(error.to_string()))?;
    Ok(
        json!({"command": command, "authenticated": true, "notebooks": notebooks.into_iter().map(|notebook| json!({"id": notebook.id, "title": notebook.title})).collect::<Vec<_>>() }),
    )
}

async fn cleanup(args: CleanupArgs) -> Result<Value, CliError> {
    if !args.all {
        return Err(CliError::usage("cleanup requires --all; it never prompts"));
    }
    let client = NotebookLMClient::from_storage_file(&args.storage.storage_file)
        .await
        .map_err(|error| CliError::failure(error.to_string()))?;
    let notebooks = client
        .list_notebooks()
        .await
        .map_err(|error| CliError::failure(error.to_string()))?;
    let mut deleted = 0;
    for notebook in notebooks {
        client
            .delete_notebook(&notebook.id)
            .await
            .map_err(|error| CliError::failure(error.to_string()))?;
        deleted += 1;
    }
    Ok(json!({"command": "cleanup", "deleted": deleted}))
}

fn print_result(json_output: bool, result: &Value) {
    if json_output {
        println!("{result}");
        return;
    }
    match result["command"].as_str() {
        Some("merge") => println!(
            "Merged {} rows; wrote {} and removed {} duplicates.",
            result["rows_before"], result["rows_written"], result["duplicates_removed"]
        ),
        Some("cleanup") => println!("Deleted {} NotebookLM notebooks.", result["deleted"]),
        Some("status" | "list") => println!(
            "NotebookLM authenticated. {} notebooks found.",
            result["notebooks"].as_array().map_or(0, Vec::len)
        ),
        Some("generate") => println!(
            "Generated flashcards for {} documents.",
            result["documents"].as_array().map_or(0, Vec::len)
        ),
        _ => println!("Completed."),
    }
}

#[derive(Debug)]
struct CliError {
    code: u8,
    message: String,
}
impl CliError {
    fn usage(error: impl Into<String>) -> Self {
        Self {
            code: EXIT_USAGE,
            message: error.into(),
        }
    }
    fn failure(error: impl Into<String>) -> Self {
        Self {
            code: EXIT_FAILURE,
            message: error.into(),
        }
    }
}
impl std::fmt::Display for CliError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn parses_explicit_relative_files() {
        assert_eq!(
            parse_files("one.pdf, two.pptx").unwrap(),
            ["one.pdf", "two.pptx"]
        );
        assert!(parse_files("../one.pdf").is_err());
    }

    #[test]
    fn finds_only_requested_supported_files() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("one.pdf"), []).unwrap();
        fs::write(directory.path().join("two.txt"), []).unwrap();
        let args = GenerateArgs {
            input_dir: directory.path().to_owned(),
            output_dir: PathBuf::new(),
            difficulty: "medium".into(),
            quantity: "standard".into(),
            language: "pt_BR".into(),
            timeout: 900,
            instructions: String::new(),
            single_cloze: false,
            include: None,
            exclude: None,
            files: None,
            storage_file: PathBuf::new(),
        };
        assert_eq!(
            source_paths(&args).unwrap(),
            [directory.path().join("one.pdf")]
        );
    }

    #[test]
    fn rejects_malformed_include_glob() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("one.pdf"), []).unwrap();
        let args = GenerateArgs {
            input_dir: directory.path().to_owned(),
            output_dir: PathBuf::new(),
            difficulty: "medium".into(),
            quantity: "standard".into(),
            language: "pt_BR".into(),
            timeout: 900,
            instructions: String::new(),
            single_cloze: false,
            include: Some("[invalid".into()),
            exclude: None,
            files: None,
            storage_file: PathBuf::new(),
        };
        let err = source_paths(&args).unwrap_err();
        assert_eq!(err.code, EXIT_USAGE);
        assert!(err.message.contains("invalid glob pattern"));
    }

    #[test]
    fn rejects_malformed_exclude_glob() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("one.pdf"), []).unwrap();
        let args = GenerateArgs {
            input_dir: directory.path().to_owned(),
            output_dir: PathBuf::new(),
            difficulty: "medium".into(),
            quantity: "standard".into(),
            language: "pt_BR".into(),
            timeout: 900,
            instructions: String::new(),
            single_cloze: false,
            include: None,
            exclude: Some("[invalid".into()),
            files: None,
            storage_file: PathBuf::new(),
        };
        let err = source_paths(&args).unwrap_err();
        assert_eq!(err.code, EXIT_USAGE);
        assert!(err.message.contains("invalid glob pattern"));
    }
}
