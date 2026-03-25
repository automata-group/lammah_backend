"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Clock, CheckCircle, XCircle, AlertCircle } from "lucide-react";

export default function HistoryPage() {
  const { user, session, loading } = useAuth();
  const router = useRouter();
  const [logs, setLogs] = useState<any[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(true);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading]);

  useEffect(() => {
    if (session) {
      fetch("http://127.0.0.1:8000/api/v1/subscription/history", {
        headers: { "Authorization": `Bearer ${session.access_token}` }
      })
      .then(r => r.json())
      .then(data => {
        setLogs(data.logs || []);
        setLoadingLogs(false);
      })
      .catch(() => setLoadingLogs(false));
    }
  }, [session]);

  if (loading || !user) return <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  const statusIcon = (s: string) => {
    if (s === "success") return <CheckCircle className="w-4 h-4 text-green-400" />;
    if (s === "failed") return <XCircle className="w-4 h-4 text-red-400" />;
    return <AlertCircle className="w-4 h-4 text-amber-400" />;
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 p-8" dir="rtl">
      <div className="max-w-3xl mx-auto">
        <button onClick={() => router.push("/dashboard")} className="text-slate-400 hover:text-indigo-300 text-sm mb-8 block">← العودة للوحة التحكم</button>

        <h1 className="text-3xl font-extrabold mb-8 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">سجل الاستخدام</h1>

        {loadingLogs ? (
          <div className="text-center py-20"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin mx-auto" /></div>
        ) : logs.length > 0 ? (
          <div className="space-y-4">
            {logs.map((log, idx) => {
              const hasData = log.metadata && log.metadata.job_id;
              return (
              <motion.div key={idx} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: idx * 0.05 }}
                onClick={() => hasData ? router.push(`/dashboard?history_id=${log.id}`) : alert("عذراً، هذا التلخيص قديم جداً ولا يدعم ميزة الاسترجاع.")}
                className={`border rounded-2xl p-5 backdrop-blur-md flex items-center justify-between transition-colors ${hasData ? 'bg-slate-800/80 border-indigo-500/30 cursor-pointer hover:bg-slate-700/80' : 'bg-slate-800/40 border-slate-700/50 grayscale opacity-80'}`}>
                <div className="flex items-center">
                  {statusIcon(log.status)}
                  <div className="mr-3">
                    <p className="font-medium text-sm">{log.action_type === "lecture_process" ? "تلخيص محاضرة" : log.action_type}</p>
                    <p className="text-xs text-slate-500">{new Date(log.created_at).toLocaleString("ar")}</p>
                  </div>
                </div>
                {log.api_cost_usd && (
                  <span className="text-xs text-slate-500 font-mono">${log.api_cost_usd}</span>
                )}
              </motion.div>
              );
            })}
          </div>
        ) : (
          <div className="bg-slate-800/40 border border-slate-700/50 rounded-3xl p-16 text-center backdrop-blur-md">
            <Clock className="w-16 h-16 text-slate-600 mx-auto mb-6" />
            <h3 className="text-xl font-bold text-slate-400 mb-3">لا يوجد سجل بعد</h3>
            <p className="text-slate-500 mb-6">ابدأ بتلخيص محاضرتك الأولى وسيظهر سجل الاستخدام هنا</p>
            <button onClick={() => router.push("/dashboard")}
              className="px-8 py-3 bg-indigo-600 hover:bg-indigo-500 text-white font-bold rounded-xl transition-all">
              ابدأ التلخيص
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
