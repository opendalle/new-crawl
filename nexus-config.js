// Public dashboard settings. The ANON key is designed to be public: row-level
// security (database/schema.sql) only lets it READ the dashboard tables.
// NEVER put the service_role key in this file or anywhere in the frontend.
// On Vercel, env vars SUPABASE_URL / SUPABASE_ANON_KEY (served by /api/config) take priority.
window.NEXUS_CONFIG = {
  supabaseUrl: "https://esnugiumktntfmvxkvwa.supabase.co",
  supabaseAnonKey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbnVnaXVta3RudGZtdnhrdndhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAxNjI2NjYsImV4cCI6MjEwNTczODY2Nn0.HqE3gBrt8HoIAwNbXJuIm7dweqDrOkDbq-SQYXMjIbk",
  chat: false
};
