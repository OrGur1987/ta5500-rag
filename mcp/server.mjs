#!/usr/bin/env node
// MCP (stdio) wrapper over the ta5500-rag HTTP service (Tel Aviv master plan תא/5500).
// Env: RAG_BASE_URL (default http://localhost:8000), RAG_API_KEY (optional, sent as X-API-Key).
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const BASE = (process.env.RAG_BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
const KEY = process.env.RAG_API_KEY || "";
const TIMEOUT_MS = 15000;

async function call(path, { method = "GET", body, query } = {}) {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const res = await fetch(url, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(KEY ? { "X-API-Key": KEY } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const text = await res.text();
  if (!res.ok) {
    const hint = res.status === 503 ? " (embedding server is down)" : "";
    throw new Error(`ta5500-rag ${method} ${path} -> HTTP ${res.status}${hint}: ${text.slice(0, 300)}`);
  }
  return JSON.parse(text);
}

const ok = (text) => ({ content: [{ type: "text", text }] });
const fail = (e) => ({
  isError: true,
  content: [{ type: "text", text: `שגיאה בגישה לשירות ה-RAG (${BASE}): ${e instanceof Error ? e.message : e}` }],
});

const server = new McpServer({ name: "ta5500-rag", version: "0.1.0" });

server.registerTool(
  "search_plan",
  {
    title: "חיפוש בתוכנית המתאר תא/5500",
    description:
      "Tel Aviv master plan (תוכנית המתאר תא/5500) text search. Use for ANY question about planning provisions, building rights, zoning, land use, " +
      "planning areas (אזורי תכנון, e.g. 703) and special provisions in table 5.4 (טבלה 5.4), building regulations and plan clauses — " +
      "this is a document source, NOT map layers, so never use the map or data-exploration agent for these questions. " +
      "Returns Hebrew plan text with citations (clause and page numbers). Answer only from the returned text and cite the clause and the page number shown for each passage. " +
      "At least one of question or a filter is required.",
    inputSchema: {
      question: z.string().optional().describe("Question in Hebrew."),
      areas: z.array(z.string()).optional().describe('Planning-area codes, e.g. "703" or sub-area "101ה".'),
      zone_types: z.array(z.string()).optional().describe('Zone-type ids like "3.3.5", category ids like "3.3", or zone names.'),
      entities: z.array(z.string()).optional().describe('Rows of table 5.4: street/special-space code like "ר-83", or a label.'),
      headings: z.array(z.string()).optional().describe('Chapters/sections by number ("4.3") or full title; see get_plan_contents.'),
    },
  },
  async ({ question, areas, zone_types, entities, headings }) => {
    try {
      const r = await call("/v1/search", {
        method: "POST",
        body: { question, areas, zone_types, entities, headings, context: true },
      });
      const head = `רמת התאמה: ${r.band_label ?? r.band}`;
      const missingHeadings = r.location?.headings_not_found ?? [];
      const warning = missingHeadings.length ? `כותרות שלא נמצאו בתכנית: ${missingHeadings.join(", ")}\n\n` : "";
      return ok(`${head}\n\n${warning}${r.context ?? JSON.stringify(r.hits, null, 2)}`);
    } catch (e) {
      return fail(e);
    }
  },
);

server.registerTool(
  "get_plan_contents",
  {
    title: "תוכן עניינים של תא/5500",
    description: "Table of contents of the plan (structure only, no plan text). Use it to learn the plan's wording and ids for the `headings` filter of search_plan.",
    inputSchema: {
      node: z.string().optional().describe('Subtree by number ("4") or full title. Omit for the whole tree.'),
      depth: z.number().int().min(1).max(6).optional().describe("Limit the levels returned."),
    },
  },
  async ({ node, depth }) => {
    try {
      return ok(JSON.stringify(await call("/v1/contents", { query: { node, depth } }), null, 2));
    } catch (e) {
      return fail(e);
    }
  },
);

server.registerTool(
  "get_plan_locations",
  {
    title: "אזורי תכנון וסוגי אזורים",
    description: "List planning areas, sub-areas, table 5.4 rows and zone types accepted by search_plan filters. Pass q to narrow every list to names containing a phrase.",
    inputSchema: { q: z.string().optional().describe("Phrase to resolve a street/area/zone name before filtering.") },
  },
  async ({ q }) => {
    try {
      return ok(JSON.stringify(await call("/v1/locations", { query: { q } }), null, 2));
    } catch (e) {
      return fail(e);
    }
  },
);

server.registerTool(
  "get_plan_chunk",
  {
    title: "קטע בודד מהתוכנית",
    description:
      "Fetch the full text of a chunk that a search result left out for size. Use the chunk_id printed in parentheses after " +
      "its citation in the 'omitted' line of the search_plan result; chunks shown in the numbered results are already complete.",
    inputSchema: { id: z.string().describe("chunk_id from the 'omitted' line of a search_plan result.") },
  },
  async ({ id }) => {
    try {
      return ok(JSON.stringify(await call("/v1/chunk", { query: { id } }), null, 2));
    } catch (e) {
      return fail(e);
    }
  },
);

await server.connect(new StdioServerTransport());
