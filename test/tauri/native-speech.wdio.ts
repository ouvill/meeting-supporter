import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { $, $$, browser, expect } from "@wdio/globals";

describe("Rust-only local speech", () => {
  it("starts without Python and retains final transcripts across page reloads", async () => {
    await $("h1=ローカル文字起こし").waitForDisplayed({ timeout: 15000 });
    const mode = await browser.tauri.execute(({ core }) =>
      core.invoke("get_runtime_mode"),
    );
    expect(mode).toBe("rust");
    const port = await browser.tauri.execute(({ core }) =>
      core.invoke("get_api_port"),
    );
    expect(port).toBeNull();
    const start = await $("button=文字起こしを開始");
    await start.waitForEnabled();
    await start.click();
    await browser.waitUntil(async () => {
      return (await $('[role="status"]').getText()).includes(
        "マイクから文字起こし中",
      );
    });
    await $("button=停止").click();
    await $("p=明日の会議は十時からです。").waitForDisplayed();
    await $("button=文字起こしを開始").waitForEnabled();
    await $("summary=認識原文").click();
    await $("p=明日の会議は十時からです").waitForDisplayed();
    await browser.saveScreenshot("/tmp/meeting-native-ui.png");

    await browser.refresh();
    await $("p=明日の会議は十時からです。").waitForDisplayed();
    await $("button=文字起こしを開始").waitForEnabled();
    await $("button=文字起こしを開始").click();
    await $("button=停止").waitForEnabled();
    await $("button=停止").click();
    await browser.waitUntil(
      async () =>
        (await $$("section[aria-label='文字起こし'] li").length) === 2,
    );
    const temporary = mkdtempSync(join(tmpdir(), "meeting-native-export-"));
    try {
      const path = join(temporary, "transcript.txt");
      await browser.tauri.execute(
        ({ core }, selectedPath) =>
          core.invoke("native_speech_export", { path: selectedPath }),
        path,
      );
      expect(readFileSync(path, "utf8")).toBe(
        "明日の会議は十時からです。\n明日の会議は十時からです。\n",
      );
    } finally {
      rmSync(temporary, { recursive: true });
    }
    await $("button=内容を消去").waitForEnabled();
    await $("button=内容を消去").click();
    await $("button=消去する").click();
    await $(
      "p=開始すると、発話が確定するたびにここへ表示されます。",
    ).waitForDisplayed();
  });
});
