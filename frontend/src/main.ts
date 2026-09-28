import "./styles.css";
import { DashboardController } from "./application/dashboard_controller";
import { HttpFlashcardsApi } from "./infrastructure/http_api";
import { BrowserDashboardView } from "./interfaces/browser_view";

const ENGLISH_CONTEXT_INSTRUCTIONS =
  "Para estudar inglês, use frases completas e naturais em inglês que apareçam nas fontes e preserve a redação original na frente. Destaque somente uma palavra ou expressão por cartão, mantendo contexto suficiente para entender seu uso. No verso, explique a expressão e traduza a frase para o idioma configurado. Considere a dificuldade escolhida e o nível informado nas instruções adicionais. Não invente frases ou atribuições às fontes. Se o material não trouxer uma frase adequada em inglês, mantenha a geração geral. Gere os cartões para exportação, sem programar revisões.";
const GENERATION_INSTRUCTIONS_LIMIT = 10_000;

const root = document.querySelector<HTMLElement>("#main-content");

if (!root) {
  throw new Error("A interface principal não foi encontrada.");
}

const api = new HttpFlashcardsApi();
const view = new BrowserDashboardView(root);
const controller = new DashboardController(api, view);

required<HTMLFormElement>(root, "auth-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const authForm = event.currentTarget as HTMLFormElement;
  const form = new FormData(authForm);
  const password = form.get("password");
  if (typeof password === "string") {
    void controller.login(password);
    authForm.reset();
  }
});

required<HTMLButtonElement>(root, "logout-button").addEventListener(
  "click",
  () => void controller.logout(),
);

required<HTMLButtonElement>(root, "connect-notebooklm").addEventListener(
  "click",
  () => void controller.connectNotebookLM(),
);

const fileInput = required<HTMLInputElement>(root, "files");
const fileError = required<HTMLParagraphElement>(root, "file-error");
const fileSummary = required<HTMLSpanElement>(root, "file-summary");
const generationForm = required<HTMLFormElement>(root, "generation-form");
const studyProfile = required<HTMLSelectElement>(root, "study-profile");
const instructionsInput = required<HTMLTextAreaElement>(root, "instructions");
fileInput.addEventListener("invalid", (event) => {
  if (fileInput.validity.valueMissing) {
    const message = "Selecione ao menos um arquivo PDF ou PPTX.";
    event.preventDefault();
    fileInput.setCustomValidity(message);
    fileInput.setAttribute("aria-invalid", "true");
    fileError.textContent = message;
    fileError.hidden = false;
  }
});
fileInput.addEventListener("change", () => {
  fileInput.setCustomValidity("");
  fileInput.removeAttribute("aria-invalid");
  fileError.hidden = true;
  const selectedFiles = fileInput.files;
  const hasFiles = Boolean(selectedFiles?.length);
  view.setGenerationFilesSelected(hasFiles);
  fileSummary.textContent = selectedFiles?.length
    ? Array.from(selectedFiles, (file) => file.name).join(", ")
    : "Nenhum arquivo selecionado";
});

generationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(generationForm);
  form.delete("study_profile");
  if (studyProfile.value === "english-context") {
    const additionalInstructions = instructionsInput.value.trim();
    const instructions = additionalInstructions
      ? `${ENGLISH_CONTEXT_INSTRUCTIONS}\n\nInstruções adicionais do usuário: ${additionalInstructions}`
      : ENGLISH_CONTEXT_INSTRUCTIONS;
    if (instructions.length > GENERATION_INSTRUCTIONS_LIMIT) {
      view.setApplicationMessage(
        "Reduza as instruções adicionais para caber junto ao perfil de inglês.",
      );
      return;
    }
    form.set("instructions", instructions);
  }
  void controller.generate(form);
});

required<HTMLUListElement>(root, "job-files").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) {
    return;
  }
  const url = target.dataset.artifactUrl;
  if (url) {
    void controller.downloadArtifact(url, target.dataset.artifactName ?? "flashcards.csv");
  }
});

void controller.initialize();

function required<T extends HTMLElement>(root: HTMLElement, id: string): T {
  const element = root.querySelector<T>(`#${id}`);
  if (!element) {
    throw new Error(`Elemento obrigatório ausente: ${id}`);
  }
  return element;
}
