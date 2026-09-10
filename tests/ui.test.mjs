import {readFile} from "node:fs/promises";
import {test} from "node:test";
import {runUiTests} from "./ui-harness.mjs";

test("dashboard DOM contract", async () => {
  const source = await readFile(new URL("../sumradio/static/app.js", import.meta.url), "utf8");
  await runUiTests(source);
});
