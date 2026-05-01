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

  const [verifying, setVerifying] = useState(false);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [verificationStep, setVerificationStep] = useState<"none" | "phone" | "code">("none");
  const [otp, setOtp] = useState("");

  const refreshInfo = () => {
    if (session) {
      setLoadingInfo(true);
      fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/subscription/me`, {
        headers: { "Authorization": `Bearer ${session.access_token}` }
      }).then(r => r.json()).then(data => { 
        setInfo(data); 
        setLoadingInfo(false); 
        if (data.profile?.phone_number) setPhoneNumber(data.profile.phone_number);
      }).catch(() => setLoadingInfo(false));
    }
  };

  useEffect(() => {
    refreshInfo();
  }, [session]);

  const handleSignOut = async () => {
    await signOut();
    router.replace("/login");
  };

  const startVerification = () => setVerificationStep("phone");

  const submitPhone = async () => {
    if (!phoneNumber) return;
    setVerifying(true);
    // In a real app, send OTP here. For now, simulate.
    setTimeout(() => {
      setVerificationStep("code");
      setVerifying(false);
    }, 1000);
  };

  const submitOtp = async () => {
    if (otp !== "1234") { // Dummy OTP
      alert("كود التحقق غير صحيح (استخدم 1234)");
      return;
    }
    setVerifying(true);
    try {
      const { createClient } = await import("@/lib/supabase");
      const supabase = createClient();
      const { error } = await supabase
        .from("profiles")
        .update({ phone_number: phoneNumber, phone_verified: true })
        .eq("id", user?.id);

      if (error) throw error;
      setVerificationStep("none");
      refreshInfo();
    } catch (err: any) {
      alert("حدث خطأ أثناء التوثيق: " + err.message);
    } finally {
      setVerifying(false);
    }
  };

  if (loading || !user) return <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  const profile = info?.profile;
  const sub = info?.active_subscription;

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 p-8 pb-20" dir="rtl">
      <div className="max-w-2xl mx-auto">
        <button onClick={() => router.push("/dashboard")} className="text-slate-400 hover:text-indigo-300 text-sm mb-8 block transition-colors">← العودة للوحة التحكم</button>

        <div className="flex justify-between items-end mb-8">
          <h1 className="text-4xl font-black bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 via-purple-300 to-indigo-400">حسابي</h1>
          <div className="text-slate-400 text-sm font-medium">معرف المستخدم: <span className="font-mono text-indigo-400/70">{user.id.slice(0, 8)}...</span></div>
        </div>

        {loadingInfo ? (
          <div className="text-center py-20"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin mx-auto" /></div>
        ) : (
          <div className="space-y-6">
            {/* Profile Card */}
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
              className="bg-slate-800/40 border border-slate-700/50 rounded-[2.5rem] p-8 backdrop-blur-xl shadow-2xl relative overflow-hidden group">
              <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/10 blur-3xl -mr-16 -mt-16 rounded-full group-hover:bg-indigo-500/20 transition-colors" />
              
              <div className="flex items-center mb-8 relative z-10">
                <div className="w-20 h-20 bg-gradient-to-br from-indigo-500/30 to-purple-500/30 rounded-3xl flex items-center justify-center ml-6 border border-indigo-500/20 shadow-inner">
                  <User className="w-10 h-10 text-indigo-300" />
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-white mb-1">{profile?.full_name || user.email?.split('@')[0] || "مستخدم لمّاح"}</h2>
                  <p className="text-indigo-300/70 text-sm font-mono tracking-wider" dir="ltr">{user.email}</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 relative z-10">
                <div className="bg-slate-900/60 rounded-2xl p-5 border border-slate-700/50 hover:border-indigo-500/30 transition-colors">
                  <div className="flex items-center text-slate-400 text-xs font-bold uppercase tracking-widest mb-3"><Phone className="w-4 h-4 ml-2 text-indigo-400" />رقم الهاتف</div>
                  <div className="flex justify-between items-center">
                    <p className="font-mono text-lg text-white" dir="ltr">{profile?.phone_number || "—"}</p>
                    {!profile?.phone_verified && (
                      <button onClick={startVerification} className="text-xs bg-indigo-500/20 text-indigo-300 px-3 py-1 rounded-full hover:bg-indigo-500/30 transition-all">تعديل</button>
                    )}
                  </div>
                </div>
                <div className="bg-slate-900/60 rounded-2xl p-5 border border-slate-700/50 hover:border-indigo-500/30 transition-colors">
                  <div className="flex items-center text-slate-400 text-xs font-bold uppercase tracking-widest mb-3"><Shield className="w-4 h-4 ml-2 text-indigo-400" />حالة التوثيق</div>
                  <div className="flex items-center">
                    {profile?.phone_verified ? (
                      <div className="flex items-center text-green-400 font-bold bg-green-400/10 px-4 py-1 rounded-full border border-green-400/20 shadow-[0_0_15px_rgba(74,222,128,0.1)]">
                        <span className="ml-2">✓</span> موثق بالهاتف
                      </div>
                    ) : (
                      <button onClick={startVerification} className="flex items-center text-amber-400 font-bold bg-amber-400/10 px-4 py-1 rounded-full border border-amber-400/20 hover:bg-amber-400/20 transition-all">
                        <span className="ml-2">⚠</span> غير موثق - وثق الآن
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* Verification Form */}
              {verificationStep !== "none" && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} className="mt-8 pt-8 border-t border-slate-700/50">
                  {verificationStep === "phone" ? (
                    <div className="space-y-4">
                      <label className="block text-sm text-slate-400 mb-2">أدخل رقم هاتفك للتوثيق:</label>
                      <div className="flex gap-2">
                        <input type="tel" value={phoneNumber} onChange={(e) => setPhoneNumber(e.target.value)}
                          placeholder="059xxxxxxx" dir="ltr"
                          className="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                        <button onClick={submitPhone} disabled={verifying} className="bg-indigo-600 px-6 py-3 rounded-xl font-bold hover:bg-indigo-500 disabled:opacity-50 transition-all">
                          {verifying ? <Loader2 className="w-5 h-5 animate-spin" /> : "إرسال كود"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-4 text-center">
                      <label className="block text-sm text-slate-400 mb-2">أدخل الكود المرسل (1234):</label>
                      <div className="flex flex-col gap-4 max-w-xs mx-auto">
                        <input type="text" value={otp} onChange={(e) => setOtp(e.target.value)} maxLength={4}
                          placeholder="× × × ×" className="bg-slate-900 border border-slate-700 rounded-xl px-4 py-4 text-center text-2xl font-black tracking-[1em] text-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                        <div className="flex gap-2">
                          <button onClick={submitOtp} disabled={verifying} className="flex-1 bg-green-600 py-3 rounded-xl font-bold hover:bg-green-500 disabled:opacity-50 transition-all">
                            {verifying ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "تأكيد التحقق"}
                          </button>
                          <button onClick={() => setVerificationStep("phone")} className="bg-slate-700 px-4 py-3 rounded-xl font-bold hover:bg-slate-600 transition-all text-xs">تغيير الرقم</button>
                        </div>
                      </div>
                    </div>
                  )}
                </motion.div>
              )}
            </motion.div>

            {/* Subscription Card */}
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
              className="bg-slate-800/40 border border-indigo-500/20 rounded-[2.5rem] p-8 backdrop-blur-xl relative overflow-hidden group">
              <div className="absolute -bottom-10 -left-10 w-40 h-40 bg-purple-500/10 blur-3xl rounded-full" />
              
              <h3 className="text-xl font-bold flex items-center mb-8 text-indigo-300 relative z-10">
                <div className="p-2 bg-indigo-500/20 rounded-lg ml-3"><Package className="w-5 h-5" /></div>
                نظام الاشتراك والحصص
              </h3>

              {sub ? (
                <div className="space-y-4 relative z-10">
                  <div className="flex justify-between items-center bg-slate-900/60 rounded-2xl p-5 border border-slate-700/30 group/item hover:bg-indigo-500/5 transition-all">
                    <span className="text-slate-400 text-sm font-medium">الباقة الحالية</span>
                    <span className="font-black text-xl text-transparent bg-clip-text bg-gradient-to-r from-indigo-300 to-purple-300">{sub.subscription_plans?.name || "باقة مخصصة"}</span>
                  </div>
                  <div className="flex justify-between items-center bg-slate-900/60 rounded-2xl p-5 border border-slate-700/30 hover:bg-indigo-500/5 transition-all">
                    <span className="text-slate-400 text-sm font-medium">الرصيد المتبقي (محاضرات)</span>
                    <div className="flex flex-col items-end">
                      <span className="font-black text-2xl text-green-400">{info?.quota_remaining || 0}</span>
                      <span className="text-[10px] text-slate-500 uppercase tracking-tighter">من أصل {sub.subscription_plans?.lecture_quota || "—"}</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="bg-slate-900/60 rounded-2xl p-5 border border-slate-700/30">
                      <div className="flex items-center text-slate-400 text-xs mb-2"><Calendar className="w-4 h-4 ml-2" />ينتهي في</div>
                      <span className="font-mono text-sm text-slate-200">{sub.expires_at ? new Date(sub.expires_at).toLocaleDateString("ar-EG", { year: 'numeric', month: 'long', day: 'numeric' }) : "—"}</span>
                    </div>
                    <div className="bg-slate-900/60 rounded-2xl p-5 border border-slate-700/30">
                      <div className="flex items-center text-slate-400 text-xs mb-2"><Shield className="w-4 h-4 ml-2" />حالة الباقة</div>
                      <span className={`font-bold ${sub.status === "active" ? "text-green-400" : "text-red-400"}`}>{sub.status === "active" ? "نشطة وموثقة" : "منتهية"}</span>
                    </div>
                  </div>
                  <button onClick={() => router.push("/plans")}
                    className="w-full py-4 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-black rounded-2xl shadow-lg shadow-indigo-900/20 transition-all mt-4 transform active:scale-[0.98]">
                    تطوير الاشتراك للحصول على ميزات أكبر
                  </button>
                </div>
              ) : (
                <div className="text-center py-10 relative z-10">
                  <div className="w-16 h-16 bg-slate-900 rounded-full flex items-center justify-center mx-auto mb-4 border border-slate-700">
                    <Package className="w-8 h-8 text-slate-600" />
                  </div>
                  <p className="text-slate-400 mb-6 font-medium italic">أنت تستخدم النسخة المجانية المحدودة حالياً</p>
                  <button onClick={() => router.push("/plans")}
                    className="px-12 py-4 bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-black rounded-2xl shadow-xl hover:shadow-indigo-500/20 transition-all transform hover:-translate-y-1">
                    اشترك الآن في إحدى الباقات
                  </button>
                </div>
              )}
            </motion.div>

            {/* Sign Out */}
            <button onClick={handleSignOut}
              className="w-full py-5 bg-red-500/5 hover:bg-red-500/10 border border-red-500/20 text-red-400 font-black rounded-2xl transition-all flex items-center justify-center group">
              <LogOut className="w-5 h-5 ml-3 group-hover:translate-x-1 transition-transform" />تسجيل الخروج من النظام
            </button>
          </div>
        )}
      </div>
    </main>
  );
}

