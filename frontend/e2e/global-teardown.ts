import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, resolve } from "node:path";

export default function globalTeardown(): void {
  const temporaryDirectory = process.env["FLASHCARDS_E2E_DIRECTORY"];
  if (!temporaryDirectory) {
    return;
  }

  const target = resolve(temporaryDirectory);
  if (
    dirname(target) !== resolve(tmpdir()) ||
    !basename(target).startsWith("flashcards-generator-e2e-")
  ) {
    throw new Error("Recusando remover diretório de E2E fora do escopo temporário.");
  }
  rmSync(target, { recursive: true, force: true });
}
