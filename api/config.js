// Vercel serverless function: hands the dashboard its PUBLIC Supabase settings.
// Set in Vercel → Project → Settings → Environment Variables:
//   SUPABASE_URL        https://<project>.supabase.co
//   SUPABASE_ANON_KEY   the *anon* key (Settings → API → "anon public")
// Never put the service_role key here: it bypasses row-level security.
module.exports = (req, res) => {
  const url = process.env.SUPABASE_URL || "";
  const anon = process.env.SUPABASE_ANON_KEY || "";
  let role = "";
  try {
    role = JSON.parse(Buffer.from(anon.split(".")[1], "base64url").toString()).role || "";
  } catch (_) { /* not a JWT (new-style publishable key) */ }
  if (role === "service_role") {
    res.status(500).json({ error: "SUPABASE_ANON_KEY is a service_role key. Use the anon/public key." });
    return;
  }
  res.setHeader("Cache-Control", "public, max-age=300");
  res.status(200).json({ supabaseUrl: url, supabaseAnonKey: anon, chat: Boolean(process.env.GEMINI_API_KEY) });
};
