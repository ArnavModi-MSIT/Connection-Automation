"use strict";
const extensionApi = globalThis.browser || globalThis.chrome;
const appUrl = extensionApi.runtime.getURL("web/index.html");
let operations = Promise.resolve();
const replacedTabs = new Map();

function currentTabId(id) {
  const seen = new Set();
  while (replacedTabs.has(id) && !seen.has(id)) {
    seen.add(id);
    id = replacedTabs.get(id);
  }
  return id;
}

async function openQueue() {
  await extensionApi.tabs.create({ url: appUrl });
}

extensionApi.action.onClicked.addListener(() => { openQueue().catch(console.error); });

async function handle(message, sender) {
  if (sender.url !== appUrl || !Number.isInteger(message.ownerTabId)) {
    throw new Error("Open the queue using this extension's toolbar button.");
  }
  // sender.url identifies our trusted extension page. Reading other tabs' URLs
  // would require broader permissions, which this extension does not request.
  const { queueLock } = await extensionApi.storage.session.get("queueLock");
  if (message.type === "queue.claim") {
    const owner = await extensionApi.tabs.get(currentTabId(message.ownerTabId));
    if (queueLock) {
      let exists = false;
      try { await extensionApi.tabs.get(currentTabId(queueLock.ownerTabId)); exists = true; } catch { /* Owner page was closed. */ }
      if (exists) throw new Error("A queue is already running. Stop it in its existing page first.");
    }
    await extensionApi.storage.session.set({ queueLock: { ownerTabId: owner.id, runId: message.runId } });
    return { ok: true };
  }
  if (message.type === "queue.release") {
    if (queueLock && currentTabId(queueLock.ownerTabId) === currentTabId(message.ownerTabId) && queueLock.runId === message.runId) {
      await extensionApi.storage.session.remove("queueLock");
    }
    return { ok: true };
  }
  throw new Error("Unknown queue action.");
}

extensionApi.runtime.onMessage.addListener((message, sender, reply) => {
  if (!message?.type?.startsWith("queue.")) return false;
  operations = operations.then(() => handle(message, sender)).then(
    (result) => reply(result), (error) => reply({ error: error.message })
  );
  return true;
});

function releasePage(tabId) {
  operations = operations.then(async () => {
    const { queueLock } = await extensionApi.storage.session.get("queueLock");
    if (queueLock?.ownerTabId === tabId) await extensionApi.storage.session.remove("queueLock");
  }).catch(console.error);
}
extensionApi.tabs.onRemoved.addListener((tabId) => releasePage(tabId));
extensionApi.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "loading") releasePage(tabId);
});
extensionApi.tabs.onReplaced?.addListener((addedId, removedId) => {
  replacedTabs.set(removedId, addedId);
  operations = operations.then(async () => {
    const { queueLock } = await extensionApi.storage.session.get("queueLock");
    if (queueLock?.ownerTabId === removedId) {
      await extensionApi.storage.session.set({ queueLock: { ...queueLock, ownerTabId: addedId } });
    }
  }).catch(console.error);
});
