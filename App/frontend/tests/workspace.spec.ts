import { test, expect } from "@playwright/test";

test("負傷者は血のマークで、同一点の複数分類も表示する", async ({
  page,
  request,
}) => {
  const run = await (
    await request.post("/api/runs", {
      data: { demo_profile_id: "tokyo", title: "新しい地図記号の確認" },
    })
  ).json();
  for (const data of [
    { title: "負傷者の搬送", map_categories: ["rescue", "injury"] },
    { title: "火災と冠水の確認", map_categories: ["fire", "flood"] },
  ]) {
    await request.post(`/api/runs/${run.id}/tasks`, {
      data: {
        ...data,
        kind: "request",
        actor: "検証",
        manual_reason: "記号の画面テスト",
        place: {
          expression: "上野公園",
          search_name: "上野公園",
          municipality: null,
          detail: null,
        },
      },
    });
  }
  await page.goto("/");
  await page.getByRole("button", { name: /新しい地図記号の確認/ }).click();
  const legend = page.getByLabel("地図記号の凡例");
  await expect(legend.locator("span")).toHaveCount(15);
  for (const symbol of ["🩸", "🚑", "🔥", "🌊", "🏚️", "📡", "🔍", "🩺", "🚻"])
    await expect(legend).toContainText(symbol);
  await expect(page.locator(".map-marker")).toHaveCount(1);
  await expect(page.locator('.pin-symbol[data-category="injury"]')).toHaveText(
    "🩸",
  );
  await expect(page.locator('.pin-symbol[data-category="rescue"]')).toHaveText(
    "🚑",
  );
  await expect(page.locator(".pin-more")).toHaveText("+1");
  await expect(page.locator(".pin small")).toHaveText("2");
  await page.locator(".map-marker").click();
  await expect(page.locator(".popup-categories").last()).toContainText(
    "🌊 浸水・冠水",
  );
  await page
    .locator(".leaflet-popup")
    .getByRole("button", { name: /負傷者の搬送/ })
    .click();
  await expect(page.locator(".task-detail")).toContainText("負傷者の搬送");
  await page
    .locator(".map-section")
    .screenshot({ path: "test-results/new-map-icons.png" });
  await page.setViewportSize({ width: 800, height: 1000 });
  await expect(legend).toBeVisible();
  await page
    .locator(".map-section")
    .screenshot({ path: "test-results/new-map-icons-narrow.png" });
});

test("追加した記号を手動選択して保存・再表示できる", async ({ page }) => {
  await page.goto("/");
  await page.locator(".profile-tokyo").click();
  await page.getByRole("button", { name: "手動登録", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "手動タスク登録" });
  await dialog
    .getByLabel("概要", { exact: true })
    .fill("複数の対応内容の記号テスト");
  await dialog.getByLabel("登録・編集の理由（根拠）").fill("画面検証");
  await dialog.getByLabel("その他", { exact: true }).uncheck();
  const labels = [
    "負傷者",
    "体調不良・医療",
    "火災・煙",
    "浸水・冠水",
    "建物被害",
    "通信障害",
    "行方不明・安否確認",
    "トイレ・衛生",
  ];
  for (const label of labels)
    await dialog.getByLabel(label, { exact: true }).check();
  await dialog.getByRole("button", { name: "保存する" }).click();
  const card = page
    .locator(".task-card")
    .filter({ hasText: "複数の対応内容の記号テスト" });
  await expect(card).toBeVisible();
  for (const label of labels) await expect(card).toContainText(label);
  await card.click();
  await page
    .locator(".task-detail")
    .getByRole("button", { name: "編集", exact: true })
    .click();
  const edit = page.getByRole("dialog", { name: "タスクを編集" });
  for (const label of labels)
    await expect(edit.getByLabel(label, { exact: true })).toBeChecked();
});

test("聞き取りの地名をワンタッチ承認してプロット・復元する（独立した模擬交信）", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /地名ワンタッチ検証/ }).click();
  const notice = page.getByLabel("聞き取りの確認候補");
  await expect(notice).toContainText("上野恩賜公園");
  await expect(notice).toContainText("代表点");
  await expect(page.locator(".leaflet-marker-icon")).toHaveCount(0);
  const before = await (
    await request.get("/api/runs/run_interpretationtest")
  ).json();
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/interpretation-location") &&
      r.request().method() === "POST",
  );
  await notice
    .getByRole("button", {
      name: "「上野恩賜公園」として承認・地図表示",
      exact: true,
    })
    .click();
  expect((await response).status()).toBe(200);
  await expect(page.locator(".leaflet-marker-icon")).toHaveCount(1);
  await expect(page.locator(".leaflet-marker-icon")).toHaveAttribute(
    "title",
    "上野恩賜公園",
  );
  await expect(notice).toContainText("場所の解釈を確認済み");
  await expect(page.locator(".task-detail")).toContainText(
    "代表点（現場の詳細位置ではありません）",
  );
  await expect(page.locator(".board-column.candidate .task-card")).toHaveCount(
    1,
  );
  await expect(page.locator(".board-column.unhandled .task-card")).toHaveCount(
    0,
  );
  await expect(page.locator(".transcript")).toHaveText(
    "こちら上野恩師公園水が足りません水1箱6本入り10箱ください",
  );
  const after = await (
    await request.get("/api/runs/run_interpretationtest")
  ).json();
  expect(after.communications).toEqual(before.communications);
  expect(after.tasks.task_interpretationtest.quantities).toEqual(
    before.tasks.task_interpretationtest.quantities,
  );
  expect(
    after.history.filter(
      (h: { action: string }) =>
        h.action === "location_interpretation_confirmed",
    ),
  ).toHaveLength(1);
  await page.screenshot({
    path: "test-results/interpretation-approved.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("button", { name: /地名ワンタッチ検証/ }).click();
  await expect(page.locator(".leaflet-marker-icon")).toHaveCount(1);
  await expect(page.getByLabel("聞き取りの確認候補")).toContainText(
    "場所の解釈を確認済み",
  );
  await expect(
    page.getByRole("button", {
      name: "「上野恩賜公園」として承認・地図表示",
      exact: true,
    }),
  ).toHaveCount(0);
});

test("起動時の対象地域を実施回ごとに保存し、候補と再開へ反映する", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByLabel("都道府県", { exact: true }).selectOption("東京都");
  await page.getByLabel("市区町村（任意）").fill("台東区");
  await expect(page.locator(".region-note")).toContainText("東京都台東区");
  await page.screenshot({
    path: "test-results/region-start.png",
    fullPage: true,
  });
  const firstResponse = page.waitForResponse(
    (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
  );
  await page.locator(".profile-tokyo").click();
  const first = await (await firstResponse).json();
  await expect(page.getByLabel("この実施回の対象地域")).toHaveText(
    "対象地域：東京都台東区",
  );
  expect(first.region).toEqual({
    prefecture: "東京都",
    municipality: "台東区",
  });
  expect(
    (await (await request.get(`/api/runs/${first.id}/locations`)).json()).map(
      (p: { id: string }) => p.id,
    ),
  ).toEqual(["tokyo_ueno"]);
  const candidate = await (
    await request.post(`/api/runs/${first.id}/tasks`, {
      data: {
        kind: "request",
        title: "地域指定を使う場所確認",
        manual_reason: "画面テスト",
        actor: "検証",
        place: {
          expression: "上野公園",
          search_name: "上野公園",
          municipality: null,
          detail: null,
        },
      },
    })
  ).json();
  await page.getByRole("button", { name: /地域指定を使う場所確認/ }).click();
  await expect(
    page.getByRole("button", { name: "この位置を確認", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".task-detail")).toContainText("上野恩賜公園");
  const original = await (await request.get(`/api/runs/${first.id}`)).json();
  expect(original.tasks[candidate.id].place.municipality).toBeNull();

  await page.getByRole("button", { name: "実施回を切り替え" }).click();
  await page.getByLabel("市区町村（任意）").fill("渋谷区");
  const secondResponse = page.waitForResponse(
    (r) => r.url().endsWith("/api/runs") && r.request().method() === "POST",
  );
  await page.locator(".profile-tokyo").click();
  const second = await (await secondResponse).json();
  await expect(page.getByLabel("この実施回の対象地域")).toHaveText(
    "対象地域：東京都渋谷区",
  );
  await request.post(`/api/runs/${second.id}/tasks`, {
    data: {
      kind: "request",
      title: "地域外の場所確認",
      manual_reason: "画面テスト",
      actor: "検証",
      place: {
        expression: "上野公園",
        search_name: "上野公園",
        municipality: null,
        detail: null,
      },
    },
  });
  await page.getByRole("button", { name: /地域外の場所確認/ }).click();
  await expect(page.locator(".task-detail")).toContainText(
    "対象地域（東京都渋谷区）内の事前登録された地点に一致しません",
  );
  await page.reload();
  await page
    .locator(".past-runs button")
    .filter({ hasText: "東京都台東区" })
    .filter({ hasText: first.title })
    .click();
  await expect(page.getByLabel("この実施回の対象地域")).toHaveText(
    "対象地域：東京都台東区",
  );
  await expect(page.locator(".board-column.candidate")).toContainText(
    "地域指定を使う場所確認",
  );
  await expect(page.locator(".board-column.candidate")).not.toContainText(
    "地域外の場所確認",
  );
});

test("誤認識の原文と解釈候補を分けて表示する（画面用模擬データ）", async ({
  page,
  request,
}) => {
  const run = await (
    await request.post("/api/runs", {
      data: { demo_profile_id: "tokyo", title: "聞き取り表示テスト" },
    })
  ).json();
  const task = await (
    await request.post(`/api/runs/${run.id}/tasks`, {
      data: {
        kind: "request",
        title: "要請品目を確認して手配",
        actor: "検証",
        manual_reason: "表示検証",
        uncertainties: ["聞き取り要確認：飲料水の可能性がありますが未確認"],
      },
    })
  ).json();
  const evidence = {
    communication_id: "comm_display_test",
    revision: 1,
    quote: "緊張水がほしいです。",
  };
  const snapshot = await (await request.get(`/api/runs/${run.id}`)).json();
  snapshot.communications.comm_display_test = {
    id: "comm_display_test",
    run_id: run.id,
    version: 1,
    revision: 1,
    received_at: run.created_at,
    original_text: "緊張水がほしいです。",
    corrections: [],
    original_audio: null,
    recognition_audio: null,
    transcription_status: "done",
    extraction_status: "done",
    extraction_revision: 1,
    metrics: {},
    error: null,
    extraction: {
      sender: null,
      recipient: null,
      situation: null,
      people: [],
      tasks: [],
      notices: [],
      phonetic_interpretations: [],
      transcript_interpretations: [
        {
          possible_meaning: "飲料水の可能性がありますが未確認",
          reason: "品目を原音で確認",
          evidence,
        },
      ],
    },
  };
  await page.route(`**/api/runs/${run.id}`, (r) =>
    r.fulfill({ json: snapshot }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: /聞き取り表示テスト/ }).click();
  await expect(page.locator(".transcript")).toHaveText("緊張水がほしいです。");
  await expect(page.getByLabel("聞き取りの確認候補")).toContainText(
    "飲料水の可能性",
  );
  await expect(page.getByLabel("聞き取りの確認候補")).toContainText(
    "原文は変更していません",
  );
  await expect(page.locator(".board-column.candidate")).toContainText(
    task.title,
  );
  await expect(page.locator(".task-card")).toContainText("内容の確認事項");
});

test("実在デモで登録、位置確認、承認、完了とピン・履歴を確認", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: /交信を記録し、\s*状況をひとつの画面に。/,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /実在地名・東京都内/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "実在地名・東京都内", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "手動登録", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "手動タスク登録" });
  await dialog.getByLabel("概要", { exact: true }).fill("給水15箱を手配");
  await dialog.getByLabel("場所", { exact: true }).fill("上野公園");
  await dialog.getByLabel("自治体", { exact: true }).fill("東京都台東区");
  await dialog.getByLabel("飲料水・給水", { exact: true }).check();
  await dialog.getByLabel("その他", { exact: true }).uncheck();
  await dialog
    .getByLabel("登録・編集の理由（根拠）")
    .fill("テスト担当者による手動登録");
  await dialog.getByRole("button", { name: "保存する" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("button", { name: /給水15箱を手配/ })
    .first()
    .click();
  await page.getByRole("button", { name: "この位置を確認" }).click();
  await expect(page.locator(".task-detail")).toContainText(
    "代表点（現場の詳細位置ではありません）",
  );
  await page.getByRole("button", { name: "承認して未対応へ" }).click();
  await page.getByRole("button", { name: "確認して変更" }).click();
  await expect(page.locator(".board-column.unhandled")).toContainText(
    "給水15箱を手配",
  );
  await expect(page.locator(".map-marker")).toHaveCount(1);
  await page.screenshot({
    path: "test-results/workspace-approved.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "対応済みにする" }).click();
  await page.getByRole("button", { name: "確認して変更" }).click();
  await expect(page.locator(".board-column.completed")).toContainText(
    "給水15箱を手配",
  );
  await expect(page.locator(".map-marker")).toHaveCount(0);
  await page.getByRole("button", { name: "操作履歴", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "操作履歴" })).toContainText(
    "位置を確認",
  );
  await expect(page.getByRole("dialog", { name: "操作履歴" })).toContainText(
    "未対応 → 対応済み",
  );
});

test("架空と実在の記録を切り替え、候補破棄後も履歴を保持", async ({
  page,
  request,
}) => {
  const real = await (
    await request.post("/api/runs", {
      data: { demo_profile_id: "tokyo", title: "分離テスト実在" },
    })
  ).json();
  const created = await (
    await request.post(`/api/runs/${real.id}/tasks`, {
      data: {
        kind: "situation_confirmation",
        title: "確認だけの候補",
        actor: "テスト",
        manual_reason: "検証",
      },
    })
  ).json();
  const fictional = await (
    await request.post("/api/runs", {
      data: { demo_profile_id: "aoba", title: "分離テスト架空" },
    })
  ).json();
  await page.goto("/");
  await page.getByRole("button", { name: /分離テスト架空/ }).click();
  await expect(page.locator(".task-card")).toHaveCount(0);
  await page.getByRole("button", { name: "実施回を切り替え" }).click();
  await page.getByRole("button", { name: /分離テスト実在/ }).click();
  await page.getByRole("button", { name: /確認だけの候補/ }).click();
  await page.getByRole("button", { name: "破棄", exact: true }).click();
  await page.getByRole("button", { name: "確認して変更" }).click();
  await expect(page.locator(".task-card")).toHaveCount(0);
  const current = await (await request.get(`/api/runs/${real.id}`)).json();
  expect(current.tasks[created.id].status).toBe("discarded");
  expect(
    Object.keys(
      (await (await request.get(`/api/runs/${fictional.id}`)).json()).tasks,
    ),
  ).toHaveLength(0);
});

test("背景地図の通信失敗でも手動操作を維持", async ({ page }) => {
  await page.route("**/tile.openstreetmap.org/**", (r) => r.abort());
  await page.goto("/");
  await page.getByRole("button", { name: /01 架空地名/ }).click();
  await expect(
    page.getByText(
      "背景地図を取得できません。保存済みの位置と一覧は利用できます。",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "手動登録", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "手動タスク登録" }),
  ).toBeVisible();
});

test("同じ地点の複数タスクをピンから個別に選べる", async ({
  page,
  request,
}) => {
  const run = await (
    await request.post("/api/runs", {
      data: { demo_profile_id: "tokyo", title: "同一点の確認" },
    })
  ).json();
  for (const title of ["給水地点の確認", "救急隊の誘導"]) {
    await request.post(`/api/runs/${run.id}/tasks`, {
      data: {
        kind: "request",
        title,
        actor: "検証",
        manual_reason: "同一点テスト",
        place: {
          expression: "上野公園",
          search_name: "上野公園",
          municipality: null,
          detail: null,
        },
      },
    });
  }
  await page.goto("/");
  await page.getByRole("button", { name: /同一点の確認/ }).click();
  await expect(page.locator(".task-card")).toHaveCount(2);
  await expect(page.locator(".map-marker")).toHaveCount(1);
  await page.locator(".map-marker").click();
  await page
    .locator(".leaflet-popup")
    .getByRole("button", { name: "救急隊の誘導" })
    .click();
  await expect(page.locator(".task-detail")).toContainText("救急隊の誘導");
});
