#!/usr/bin/env node
/**
 * Engineer → Pi SDK handoff for one execution-plan item.
 *
 * Pipeline:
 *   1. Write tests from the feature/bug spec (do not implement yet).
 *   2. Implement the feature/bug fix.
 *   3. Run the predetermined tests.
 *   4. If they fail, return to step 2 with that output; if they pass, summarize.
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
} from "@earendil-works/pi-coding-agent";

function argValue(flag, fallback = "") {
  const idx = process.argv.indexOf(flag);
  if (idx < 0 || idx + 1 >= process.argv.length) return fallback;
  return process.argv[idx + 1];
}

const cwd = resolve(argValue("--cwd"));
const itemDir = resolve(argValue("--item-dir"));
const resultPath = resolve(argValue("--result", join(itemDir, ".pi-result.json")));
const maxRounds = Math.max(1, Number(argValue("--max-rounds", "5")) || 5);
const pythonBin = argValue("--python", process.env.PYTHON || "python3");

if (!cwd || !itemDir) {
  console.error("usage: run_item.mjs --cwd <service-dir> --item-dir <item-dir> [--max-rounds N] [--result path]");
  process.exit(2);
}

const specPath = join(itemDir, "SPEC.md");
const spec = existsSync(specPath) ? readFileSync(specPath, "utf8") : "";

function writeResult(payload) {
  writeFileSync(resultPath, JSON.stringify(payload, null, 2), "utf8");
}

function lastAssistantText(messages) {
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message || message.role !== "assistant") continue;
    const content = message.content;
    if (typeof content === "string" && content.trim()) return content.trim();
    if (!Array.isArray(content)) continue;
    const text = content
      .filter((part) => part && (part.type === "text" || typeof part.text === "string"))
      .map((part) => part.text || "")
      .join("")
      .trim();
    if (text) return text;
  }
  return "";
}

function runPredeterminedTests() {
  const result = spawnSync(
    pythonBin,
    [
      "-m",
      "unittest",
      "discover",
      "-s",
      itemDir,
      "-p",
      "test_*.py",
      "-q",
    ],
    {
      cwd: itemDir,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: itemDir },
      timeout: 120_000,
    },
  );
  const output = `${result.stdout || ""}\n${result.stderr || ""}`.trim();
  if (result.error) {
    return { ok: false, output: result.error.message || String(result.error) };
  }
  return { ok: result.status === 0, output: output.slice(0, 8000) };
}

const systemPrompt = [
  "You are the coding implementer for one microservice, invoked by the Engineer sub-agent.",
  `Working directory: ${cwd}`,
  `Current plan item directory: ${itemDir}`,
  "Stay inside this working directory. Do not git commit or git push.",
  "Do not weaken, delete, or skip tests after they are written.",
  "Prefer unittest modules named test_*.py under the current item directory so the engineer can re-run them.",
].join("\n");

const modelRuntime = await ModelRuntime.create();
const loader = new DefaultResourceLoader({
  cwd,
  systemPromptOverride: () => systemPrompt,
});
await loader.reload();

const { session } = await createAgentSession({
  cwd,
  resourceLoader: loader,
  tools: ["read", "write", "edit", "bash", "grep", "find", "ls"],
  sessionManager: SessionManager.inMemory(cwd),
  modelRuntime,
});

let streamed = "";
session.subscribe((event) => {
  if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
    streamed += event.assistantMessageEvent.delta || "";
  }
});

try {
  await session.prompt(
    [
      "STEP 1 — WRITE TESTS ONLY.",
      "Read SPEC.md in the item directory and any existing service files.",
      "Write unittest files named test_*.py in the item directory that verify the feature or bug fix.",
      "Do not implement production code yet. Tests may fail until the next step.",
      "",
      spec,
    ].join("\n"),
  );

  await session.prompt(
    [
      "STEP 2 — IMPLEMENT.",
      "Write the production code that makes the tests from step 1 pass.",
      "Do not change the predetermined tests unless they have a clear syntax error.",
      "Keep the change scoped to this plan item.",
    ].join("\n"),
  );

  let lastTests = { ok: false, output: "Tests were not run." };
  let rounds = 0;
  for (let i = 0; i < maxRounds; i += 1) {
    rounds = i + 1;
    lastTests = runPredeterminedTests();
    if (lastTests.ok) break;
    await session.prompt(
      [
        "STEP 2 AGAIN — the predetermined tests still fail. Fix the implementation only.",
        "Do not delete or weaken tests.",
        "",
        lastTests.output || "Tests failed with no output.",
      ].join("\n"),
    );
  }

  if (!lastTests.ok) {
    writeResult({
      ok: false,
      rounds,
      summary: lastAssistantText(session.messages) || streamed.trim().slice(-2000),
      test_output: lastTests.output,
      error: `Predetermined tests still failed after ${rounds} implementation round(s).`,
    });
    session.dispose();
    process.exit(1);
  }

  streamed = "";
  await session.prompt(
    [
      "STEP 4 — SUMMARIZE for the engineer sub-agent.",
      "The predetermined tests passed. Reply with a concise summary of:",
      "- tests added",
      "- code changed",
      "- remaining risks",
      "Do not edit files.",
    ].join("\n"),
  );

  writeResult({
    ok: true,
    rounds,
    summary: lastAssistantText(session.messages) || streamed.trim(),
    test_output: lastTests.output,
    error: "",
  });
  session.dispose();
  process.exit(0);
} catch (err) {
  writeResult({
    ok: false,
    rounds: 0,
    summary: lastAssistantText(session.messages) || streamed.trim(),
    test_output: "",
    error: err instanceof Error ? err.message : String(err),
  });
  try {
    session.dispose();
  } catch {
    /* ignore */
  }
  process.exit(1);
}
