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
import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
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
const questionsPath = join(itemDir, ".pi-questions.json");
const answersPath = join(itemDir, ".pi-answers.json");

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

function readQuestionsFile() {
  if (!existsSync(questionsPath)) return [];
  try {
    const data = JSON.parse(readFileSync(questionsPath, "utf8"));
    const raw = data && data.questions;
    if (!Array.isArray(raw)) return [];
    return raw.map((item) => String(item || "").trim()).filter(Boolean);
  } catch {
    return [];
  }
}

function extractQuestions(text) {
  const body = String(text || "");
  const marker = body.match(/PI_NEED_USER\b/);
  if (!marker) return [];
  const after = body.slice(marker.index + "PI_NEED_USER".length);
  return after
    .split("\n")
    .map((line) => line.replace(/^[\s*#\-0-9.]+/, "").trim())
    .filter((line) => line && !line.startsWith("```"));
}

function writeQuestions(questions) {
  writeFileSync(questionsPath, JSON.stringify({ questions }, null, 2), "utf8");
}

function sleep(ms) {
  return new Promise((resolveSleep) => {
    setTimeout(resolveSleep, ms);
  });
}

async function waitForAnswers() {
  while (true) {
    if (existsSync(answersPath)) {
      const raw = readFileSync(answersPath, "utf8");
      try {
        const data = JSON.parse(raw);
        return String((data && (data.answers || data.answer)) || raw).trim();
      } catch {
        return raw.trim();
      }
    }
    await sleep(1000);
  }
}

function clearHandshake() {
  try {
    unlinkSync(questionsPath);
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(answersPath);
  } catch {
    /* ignore */
  }
}

const systemPrompt = [
  "You are the coding implementer for one microservice, invoked by the Engineer sub-agent.",
  `Working directory: ${cwd}`,
  `Current plan item directory: ${itemDir}`,
  "Stay inside this working directory. Do not git commit or git push.",
  "Do not weaken, delete, or skip tests after they are written.",
  "Prefer unittest modules named test_*.py under the current item directory so the engineer can re-run them.",
  "If you cannot continue without a product or design decision from the user:",
  "1. Write a JSON file at the item directory named .pi-questions.json with {\"questions\": [\"...\"]}.",
  "2. Also reply with a line PI_NEED_USER followed by one question per line.",
  "3. Stop editing and wait. Do not guess. The engineer will collect answers and send them back.",
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

async function promptAndMaybeAsk(text) {
  await session.prompt(text);
  let questions = readQuestionsFile();
  if (!questions.length) {
    questions = extractQuestions(lastAssistantText(session.messages) || streamed);
    if (questions.length) writeQuestions(questions);
  }
  if (!questions.length) return;
  const answers = await waitForAnswers();
  clearHandshake();
  streamed = "";
  await session.prompt(
    [
      "The product owner answered your questions. Continue the current step with these answers.",
      "Do not ask the same questions again unless they are still blocking.",
      "",
      answers || "(no answer text)",
    ].join("\n"),
  );
}

try {
  await promptAndMaybeAsk(
    [
      "STEP 1 — WRITE TESTS ONLY.",
      "Read SPEC.md in the item directory and any existing service files.",
      "Write unittest files named test_*.py in the item directory that verify the feature or bug fix.",
      "Do not implement production code yet. Tests may fail until the next step.",
      "",
      spec,
    ].join("\n"),
  );

  await promptAndMaybeAsk(
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
    await promptAndMaybeAsk(
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
  await promptAndMaybeAsk(
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
