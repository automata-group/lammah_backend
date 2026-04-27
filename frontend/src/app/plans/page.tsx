"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, BookOpen, Zap, Crown, Star, Clock, Check } from "lucide-react";

export default function PlansPage() {
  const { user, session, loading } = useAuth();
  const router = useRouter();
  const [plans, setPlans] = useState<any>({ weekly: [], monthly: [] });
  const [loadingPlans, setLoadingPlans] = useState(true);
  const [subscribing, setSubscribing] = useState("");
  const [tab, setTab] = useState<"monthly" | "weekly">("monthly");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading]);

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/subscription/plans`)
      .then(r => r.json())
      .then(data => { setPlans(data); setLoadingPlans(false); })
      .catch(() => setLoadingPlans(false));
  }, []);

  const handleSubscribe = (planId: string) => {
    if (!session) return;
    router.push(`/checkout/${planId}`);
  };

  const icons: Record<string, any> = { 
    "أسبوعي - طالب": Star, "أسبوعي - متقدم": Zap, "أسبوعي - VIP": Crown, 
    "شهري - أساسي": Star, "شهري - متقدم": Zap, "شهري - برو": Crown, "شهري - غير محدود": Crown 
  };
  const colors: Record<string, string> = { 
    "أسبوعي - طالب": "indigo", "شهري - أساسي": "indigo", 
    "أسبوعي - متقدم": "purple", "شهري - متقدم": "purple", 
    "أسبوعي - VIP": "amber", "شهري - برو": "amber", "شهري - غير محدود": "amber" 
  };

  if (loading || !user) return <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  const activePlans = tab === "monthly" ? plans.monthly : plans.weekly;

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 p-8" dir="rtl">
      <div className="max-w-5xl mx-auto">
        <button onClick={() => router.push("/dashboard")} className="text-slate-400 hover:text-indigo-300 text-sm mb-8 block">← العودة للوحة التحكم</button>
        
        <div className="text-center mb-12">
          <h1 className="text-4xl font-extrabold mb-4 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">اختر باقتك</h1>
          <p className="text-slate-400">اشترك الآن وابدأ بتلخيص محاضراتك واستخراج الأسئلة عبر محركنا الذكي</p>
        </div>

        {/* Tab Toggle */}
        <div className="flex justify-center mb-10">
          <div className="bg-slate-800/80 border border-slate-700 rounded-2xl p-1.5 flex">
            <button onClick={() => setTab("monthly")}
              className={`px-8 py-3 rounded-xl font-bold text-sm transition-all ${tab === "monthly" ? "bg-indigo-600 text-white shadow-lg" : "text-slate-400 hover:text-white"}`}>
              <Clock className="w-4 h-4 inline ml-1" />شهري
            </button>
            <button onClick={() => setTab("weekly")}
              className={`px-8 py-3 rounded-xl font-bold text-sm transition-all ${tab === "weekly" ? "bg-indigo-600 text-white shadow-lg" : "text-slate-400 hover:text-white"}`}>
              <Clock className="w-4 h-4 inline ml-1" />أسبوعي
            </button>
          </div>
        </div>

        {message && (
          <div className={`text-center mb-6 p-4 rounded-xl border ${message.includes("✅") || message.includes("🎉") ? "bg-green-500/10 border-green-500/30 text-green-300" : "bg-red-500/10 border-red-500/30 text-red-300"}`}>{message}</div>
        )}

        {loadingPlans ? (
          <div className="text-center py-20"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin mx-auto" /></div>
        ) : (
          <div className="grid md:grid-cols-3 gap-6">
            {activePlans.map((plan: any, idx: number) => {
              const Icon = icons[plan.name] || Star;
              const color = colors[plan.name] || "indigo";
              const isPopular = idx === 1;
              
              return (
                <motion.div key={plan.id} initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: idx * 0.1 }}
                  className={`relative bg-slate-800/60 border rounded-3xl p-8 backdrop-blur-md transition-all hover:scale-[1.02] ${isPopular ? "border-purple-500/60 shadow-[0_0_30px_rgba(168,85,247,0.15)]" : "border-slate-700/50"}`}>
                  
                  {isPopular && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-gradient-to-r from-purple-600 to-indigo-600 text-white text-xs font-bold px-5 py-1.5 rounded-full shadow-lg">
                      الأكثر طلباً 🔥
                    </div>
                  )}

                  <div className={`w-14 h-14 rounded-2xl bg-${color}-500/20 flex items-center justify-center mb-5`}>
                    <Icon className={`w-7 h-7 text-${color}-400`} />
                  </div>

                  <h3 className="text-xl font-bold mb-2">{plan.name}</h3>
                  <div className="flex items-baseline mb-6">
                    <span className="text-4xl font-extrabold text-white">₪{plan.price_ils}</span>
                    <span className="text-slate-400 text-sm mr-2">/{tab === "monthly" ? "شهر" : "أسبوع"}</span>
                  </div>

                  <div className="space-y-3 mb-8">
                    <div className="flex items-center text-sm text-slate-300">
                      <Check className="w-4 h-4 text-green-400 ml-2 flex-shrink-0" />
                      <span className="font-bold">{plan.lecture_quota} محاضرة</span>
                    </div>
                    {/* Chat Limit Logic */}
                    <div className="flex items-center text-sm text-blue-300 bg-blue-500/5 p-2 rounded-lg border border-blue-500/10">
                      <Check className="w-4 h-4 text-blue-400 ml-2 flex-shrink-0" />
                      <span>محادثة ذكية: {
                        plan.name.includes("متقدم") ? "40 رسالة / محاضرة" : 
                        (plan.name.includes("VIP") || plan.name.includes("برو") || plan.name.includes("غير محدود")) ? "100 رسالة / محاضرة" : 
                        "15 رسالة / محاضرة"
                      }</span>
                    </div>
                    {plan.features && (Array.isArray(plan.features) ? plan.features : (() => { try { return JSON.parse(plan.features); } catch { return []; } })()).map((f: string, i: number) => (
                      <div key={i} className="flex items-center text-sm text-slate-300">
                        <Check className="w-4 h-4 text-green-400 ml-2 flex-shrink-0" />
                        <span>{f}</span>
                      </div>
                    ))}
                  </div>

                  <button onClick={() => handleSubscribe(plan.id)} disabled={!!subscribing}
                    className={`w-full py-4 rounded-xl font-bold transition-all ${isPopular ? "bg-gradient-to-r from-purple-600 to-indigo-600 text-white shadow-lg hover:shadow-purple-500/30" : "bg-slate-700 text-slate-200 hover:bg-slate-600"} disabled:opacity-50`}>
                    {subscribing === plan.id ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "اشترك الآن"}
                  </button>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}
