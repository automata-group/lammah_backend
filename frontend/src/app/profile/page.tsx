"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, User, Phone, Package, Calendar, LogOut, Shield } from "lucide-react";

export default function ProfilePage() {
  const { user, session, loading, signOut } = useAuth();
  const router = useRouter();
  const [info, setInfo] = useState<any>(null);
  const [loadingInfo, setLoadingInfo] = useState(true);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading]);

  useEffect(() => {
    if (session) {
      fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/subscription/me`, {
        headers: { "Authorization": `Bearer ${session.access_token}` }
      }).then(r => r.json()).then(data => { setInfo(data); setLoadingInfo(false); }).catch(() => setLoadingInfo(false));
    }
  }, [session]);

  const handleSignOut = async () => {
    await signOut();
    router.replace("/login");
  };

  if (loading || !user) return <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  const profile = info?.profile;
  const sub = info?.active_subscription;

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 p-8" dir="rtl">
      <div className="max-w-2xl mx-auto">
        <button onClick={() => router.push("/dashboard")} className="text-slate-400 hover:text-indigo-300 text-sm mb-8 block">← العودة للوحة التحكم</button>

        <h1 className="text-3xl font-extrabold mb-8 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">الملف الشخصي</h1>

        {loadingInfo ? (
          <div className="text-center py-20"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin mx-auto" /></div>
        ) : (
          <div className="space-y-6">
            {/* Profile Card */}
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
              className="bg-slate-800/60 border border-slate-700/50 rounded-3xl p-8 backdrop-blur-md">
              <div className="flex items-center mb-6">
                <div className="w-16 h-16 bg-indigo-500/20 rounded-full flex items-center justify-center ml-5">
                  <User className="w-8 h-8 text-indigo-400" />
                </div>
                <div>
                  <h2 className="text-xl font-bold">{profile?.full_name || "طالب جامعي"}</h2>
                  <p className="text-slate-400 text-sm font-mono" dir="ltr">{user.phone || profile?.phone_number || "—"}</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                  <div className="flex items-center text-slate-400 text-xs mb-2"><Phone className="w-3 h-3 ml-1" />الهاتف</div>
                  <p className="font-mono text-sm" dir="ltr">{user.phone || "—"}</p>
                </div>
                <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                  <div className="flex items-center text-slate-400 text-xs mb-2"><Shield className="w-3 h-3 ml-1" />الحالة</div>
                  <p className="text-sm">{profile?.phone_verified ? <span className="text-green-400">✓ موثق</span> : <span className="text-amber-400">غير موثق</span>}</p>
                </div>
              </div>
            </motion.div>

            {/* Subscription Card */}
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
              className="bg-slate-800/60 border border-indigo-500/30 rounded-3xl p-8 backdrop-blur-md">
              <h3 className="text-lg font-bold flex items-center mb-6 text-indigo-300">
                <Package className="w-5 h-5 ml-2" />الاشتراك الحالي
              </h3>

              {sub ? (
                <div className="space-y-4">
                  <div className="flex justify-between items-center bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                    <span className="text-slate-400 text-sm">الباقة</span>
                    <span className="font-bold text-indigo-200">{sub.subscription_plans?.name || "—"}</span>
                  </div>
                  <div className="flex justify-between items-center bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                    <span className="text-slate-400 text-sm">المحاضرات المتبقية</span>
                    <span className="font-bold text-green-400">{info?.quota_remaining || 0}</span>
                  </div>
                  <div className="flex justify-between items-center bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                    <span className="text-slate-400 text-sm">تاريخ الانتهاء</span>
                    <span className="font-mono text-sm text-slate-300">{sub.expires_at ? new Date(sub.expires_at).toLocaleDateString("ar") : "—"}</span>
                  </div>
                  <div className="flex justify-between items-center bg-slate-900/50 rounded-xl p-4 border border-slate-800">
                    <span className="text-slate-400 text-sm">الحالة</span>
                    <span className={`font-bold ${sub.status === "active" ? "text-green-400" : "text-red-400"}`}>{sub.status === "active" ? "✓ نشط" : "منتهي"}</span>
                  </div>
                  <button onClick={() => router.push("/plans")}
                    className="w-full py-3 bg-indigo-600 hover:bg-indigo-500 text-white font-bold rounded-xl transition-all mt-4">
                    ترقية الباقة
                  </button>
                </div>
              ) : (
                <div className="text-center py-8">
                  <p className="text-slate-400 mb-4">لا يوجد اشتراك نشط حالياً</p>
                  <button onClick={() => router.push("/plans")}
                    className="px-8 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-bold rounded-xl shadow-lg">
                    اشترك الآن
                  </button>
                </div>
              )}
            </motion.div>

            {/* Sign Out */}
            <button onClick={handleSignOut}
              className="w-full py-4 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-300 font-bold rounded-xl transition-all flex items-center justify-center">
              <LogOut className="w-5 h-5 ml-2" />تسجيل الخروج
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
