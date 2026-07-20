import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export interface AcpFixture {
  directory: string;
  statePath: string;
  command: string[];
}

export async function createAcpFixture({
  initialInvocation,
}: {
  initialInvocation?: number;
} = {}): Promise<AcpFixture> {
  const directory = await mkdtemp(
    join(tmpdir(), "meeting-supporter-wdio-acp-"),
  );
  const statePath = join(directory, "invocations");
  if (initialInvocation !== undefined) {
    await writeFile(statePath, String(initialInvocation), "utf-8");
  }
  const pythonExecutable = resolve(
    "python",
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  const agent = resolve("test/tauri/fixtures/wdio_acp_agent.py");
  return {
    directory,
    statePath,
    command: [pythonExecutable, agent, statePath],
  };
}

export async function removeAcpFixture(fixture: AcpFixture): Promise<void> {
  await rm(fixture.directory, { recursive: true, force: true });
}
