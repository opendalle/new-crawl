// Vercel serverless function: "Ask Nexus" chat, answered ONLY from stored signals.
// The Gemini key stays on the server (env GEMINI_API_KEY) — v4 shipped it in the HTML.
// Env: GEMINI_API_KEY, GEMINI_MODEL (default gemini-2.5-flash), SUPABASE_URL, SUPABASE_ANON_KEY
const hits = new Map(); // best-effort per-instance rate limit

function clip(s, n) { return String(s || "").replace(/\s+/g, " ").slice(0, n); }

async function sb(path) {
  const url = `${process.env.SUPABASE_URL}/rest/v1/${path}`;
  const key = process.env.SUPABASE_ANON_KEY;
  const r = await fetch(url, { headers: { apikey: key, Authorization: `Bearer ${key}` } });
  if (!r.ok) throw new Error(`supabase ${r.status}`);
  return r.json();
}

module.exports = async (req, res) => {
  if (req.method !== "POST") { res.status(405).json({ error: "POST only" }); return; }
  if (!process.env.GEMINI_API_KEY) { res.status(503).json({ error: "Chat is not configured (GEMINI_API_KEY missing)." }); return; }

  const ip = (req.headers["x-forwarded-for"] || "").split(",")[0] || "anon";
  const now = Date.now();
  const recent = (hits.get(ip) || []).filter((t) => now - t < 60_000);
  if (recent.length >= 8) { res.status(429).json({ error: "Too many questions — wait a minute." }); return; }
  hits.set(ip, [...recent, now]);

  const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : (req.body || {});
  const question = clip(body.question, 500).trim();
  if (!question) { res.status(400).json({ error: "Empty question" }); return; }

  let signals = [], events = [];
  try {
    signals = await sb("signal_feed?select=company_name,signal_type,location,country,confidence_score,evidence,summary,source_url,data_source,created_at&order=created_at.desc&limit=80");
    events = await sb("event_calendar?select=company_name,event_type,event_date,purpose,source_url&cre_relevant=eq.true&order=event_date.asc&limit=30");
  } catch (e) {
    res.status(502).json({ error: "Could not load signals: " + e.message }); return;
  }
  const sources = [];
  const lines = [];
  signals.forEach((s) => {
    sources.push({ id: `S${sources.length + 1}`, company: s.company_name, url: s.source_url });
    lines.push(`[S${sources.length}] ${s.signal_type} | ${s.company_name} | ${s.location || "-"}, ${s.country || "-"} | ${String(s.created_at || "").slice(0, 10)} | "${clip(s.evidence || s.summary, 240)}"`);
  });
  events.forEach((e) => {
    sources.push({ id: `S${sources.length + 1}`, company: e.company_name, url: e.source_url });
    lines.push(`[S${sources.length}] UPCOMING ${e.event_type} ${e.event_date || "date n/a"} | ${e.company_name} | ${clip(e.purpose, 200)}`);
  });

  const prompt = `You are the Nexus Asia CRE analyst. Answer the question using ONLY the records below.
Rules: cite every fact with its record id like [S3]. If the records do not contain the answer, say
"The current feed has no record of that." Do not use outside knowledge. Do not invent companies,
numbers or dates. 2-5 sentences, plain text.

RECORDS (${lines.length}):
${lines.join("\n") || "(none)"}

QUESTION: ${question}`;

  try {
    const model = process.env.GEMINI_MODEL || "gemini-2.5-flash";
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-goog-api-key": process.env.GEMINI_API_KEY },
      body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }],
                             generationConfig: { temperature: 0.1, maxOutputTokens: 500 } }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data?.error?.message || `gemini ${r.status}`);
    const answer = data?.candidates?.[0]?.content?.parts?.map((p) => p.text).join("") || "No answer.";
    const cited = [...new Set((answer.match(/S\d+/g) || []))];
    res.status(200).json({ answer, sources: sources.filter((s) => cited.includes(s.id)) });
  } catch (e) {
    res.status(502).json({ error: "Model error: " + clip(e.message, 160) });
  }
};
