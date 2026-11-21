import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("search returns the RAG data and preserves the text and PDF marker", async () => {
  const searchResult = {
    band: "high",
    band_label: "High",
    location: { areas: ["703"], headings_not_found: [] },
    hits: [{ chunk_id: "4.3", citation: "4.3", page_start: 65, page_end: 66, score: 0.75 }],
    context: "Plan context",
  };
  const api = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(searchResult));
  });
  api.listen(0, "127.0.0.1");
  await once(api, "listening");
  const address = api.address();
  assert.ok(address && typeof address !== "string");

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL("./server.mjs", import.meta.url))],
    env: { ...process.env, RAG_BASE_URL: `http://127.0.0.1:${address.port}`, RAG_API_KEY: "" },
  });
  const client = new Client({ name: "test-client", version: "1.0.0" });
  try {
    await client.connect(transport);
    const result = await client.callTool({ name: "search_plan", arguments: { headings: ["4.3"] } });
    assert.deepEqual(result.structuredContent, searchResult);
    assert.equal(result.content[0].text, 'רמת התאמה: High\n\nPlan context\n\n<!--ta5500-hits:[{"id":"4.3","p":65,"s":0.75}]-->');
  } finally {
    await client.close();
    await new Promise((resolve, reject) => api.close((error) => error ? reject(error) : resolve()));
  }
});
