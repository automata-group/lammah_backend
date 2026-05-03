"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, BookOpen, ShieldCheck, Mail, Lock, UserPlus, LogIn } from "lucide-react";

export default function LoginPage() {
  const { user, loading, signInWithGoogle, signInWithEmail, signUpWithEmail, isLocalDev } = useAuth();
  const router = useRouter();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);

  // حالات النموذج المحلي
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSignUp, setIsSignUp] = useState(false);
  const [localSuccess, setLocalSuccess] = useState("");

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [user, loading]);

  const handleGoogleLogin = async () => {
    setSubmitting(true);
    setError("");
    const { error } = await signInWithGoogle();
    if (error) {
      setError(error.message || "فشل تسجيل الدخول عبر جوجل.");
      setSubmitting(false);
    }
  };

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    setLocalSuccess("");

    if (!email || !password) {
      setError("يرجى ملء جميع الحقول.");
      setSubmitting(false);
      return;
    }

    if (isSignUp) {
      const { error } = await signUpWithEmail(email, password);
      if (error) {
        setError(error.message);
      } else {
        setLocalSuccess("✅ تم إنشاء الحساب! تحقق من بريدك الإلكتروني للتأكيد، أو سجّل دخول مباشرة.");
        setIsSignUp(false);
      }
    } else {
      const { error } = await signInWithEmail(email, password);
      if (error) {
        setError(error.message);
      }
    }
    setSubmitting(false);
  };

  if (loading) return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center">
      <Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />
    </div>
  );

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 flex flex-col items-center justify-center p-8 overflow-hidden relative" dir="rtl">
      {/* Decorative Elements */}
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none">
        <div className="absolute top-[-10%] right-[-10%] w-[50%] h-[50%] bg-indigo-500/10 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-10%] left-[-10%] w-[50%] h-[50%] bg-purple-500/10 blur-[120px] rounded-full" />
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }} 
        animate={{ opacity: 1, y: 0 }} 
        className="w-full max-w-md relative z-50"
      >
        <div className="text-center mb-10">
          <motion.img 
            initial={{ scale: 0.8 }}
            animate={{ scale: 1 }}
            src="/lammah-logo.png" 
            alt="Lammah Logo" 
            className="w-28 h-28 mx-auto mb-6 object-contain drop-shadow-[0_0_20px_rgba(99,102,241,0.3)]" 
          />
          <h1 className="text-4xl font-black mb-3 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 via-purple-300 to-indigo-100 tracking-tight">
            منصة لمّاح
          </h1>
          <p className="text-slate-400 text-base font-medium">مرحباً بك مجدداً في مستقبلك الدراسي</p>
          {isLocalDev && (
            <span className="inline-block mt-2 px-3 py-1 text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded-full">
              🛠️ وضع التطوير المحلي
            </span>
          )}
        </div>

        <div className="bg-slate-800/40 border border-slate-700/50 rounded-[2.5rem] p-10 backdrop-blur-2xl shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 right-0 w-24 h-24 bg-indigo-500/5 blur-2xl -mr-12 -mt-12 rounded-full" />
          
          <div className="flex items-center justify-center mb-10 text-indigo-300 bg-indigo-500/10 py-2 rounded-full border border-indigo-500/20">
            <ShieldCheck className="w-4 h-4 ml-2" />
            <span className="text-xs font-bold uppercase tracking-wider">دخول آمن وموثق</span>
          </div>

          <div className="space-y-6">
            {error && (
              <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-2xl p-4 text-center font-medium">
                {error}
              </motion.div>
            )}

            {localSuccess && (
              <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="text-green-400 text-sm bg-green-500/10 border border-green-500/30 rounded-2xl p-4 text-center font-medium">
                {localSuccess}
              </motion.div>
            )}

            {/* ===== نموذج الإيميل/الباسوورد (محلي فقط) ===== */}
            {isLocalDev && (
              <form onSubmit={handleEmailLogin} className="space-y-4">
                <div className="relative">
                  <Mail className="absolute right-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
                  <input
                    type="email"
                    placeholder="البريد الإلكتروني"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full bg-slate-700/50 border border-slate-600/50 rounded-2xl py-4 pr-12 pl-4 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all"
                    dir="ltr"
                  />
                </div>
                <div className="relative">
                  <Lock className="absolute right-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
                  <input
                    type="password"
                    placeholder="كلمة المرور"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-slate-700/50 border border-slate-600/50 rounded-2xl py-4 pr-12 pl-4 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all"
                    dir="ltr"
                  />
                </div>
                <button 
                  type="submit"
                  disabled={submitting}
                  className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-4 rounded-2xl shadow-lg hover:shadow-indigo-500/25 transition-all disabled:opacity-50 flex items-center justify-center gap-3 active:scale-[0.98]"
                >
                  {submitting ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : isSignUp ? (
                    <><UserPlus className="w-5 h-5" /> إنشاء حساب جديد</>
                  ) : (
                    <><LogIn className="w-5 h-5" /> تسجيل الدخول</>
                  )}
                </button>
                <button 
                  type="button"
                  onClick={() => { setIsSignUp(!isSignUp); setError(""); setLocalSuccess(""); }}
                  className="w-full text-sm text-indigo-400 hover:text-indigo-300 transition-colors font-bold py-2"
                >
                  {isSignUp ? "لديك حساب بالفعل؟ سجّل دخول" : "ليس لديك حساب؟ أنشئ واحداً"}
                </button>

                <div className="flex items-center gap-4 py-2">
                  <div className="flex-1 h-px bg-slate-700/50"></div>
                  <span className="text-xs text-slate-500 font-bold">أو</span>
                  <div className="flex-1 h-px bg-slate-700/50"></div>
                </div>
              </form>
            )}

            {/* ===== زر جوجل (دائماً متاح) ===== */}
            <button 
              onClick={handleGoogleLogin} 
              disabled={submitting}
              className="w-full bg-white text-slate-900 hover:bg-slate-50 font-black py-4.5 rounded-2xl shadow-[0_10px_20px_rgba(255,255,255,0.1)] hover:shadow-[0_15px_30px_rgba(255,255,255,0.15)] transition-all disabled:opacity-50 flex items-center justify-center gap-3 active:scale-[0.98]"
            >
              {submitting ? (
                <Loader2 className="w-6 h-6 animate-spin mx-auto text-slate-400" />
              ) : (
                <>
                  <svg className="w-6 h-6" viewBox="0 0 24 24">
                    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                  </svg>
                  المتابعة باستخدام Google
                </>
              )}
            </button>

            {/* Remember Me Toggle */}
            <div className="flex items-center justify-between px-2">
              <label className="flex items-center cursor-pointer group">
                <div className="relative">
                  <input 
                    type="checkbox" 
                    className="sr-only" 
                    checked={rememberMe} 
                    onChange={() => setRememberMe(!rememberMe)} 
                  />
                  <div className={`block w-10 h-6 rounded-full transition-colors ${rememberMe ? 'bg-indigo-600' : 'bg-slate-700'}`}></div>
                  <div className={`dot absolute left-1 top-1 bg-white w-4 h-4 rounded-full transition-transform ${rememberMe ? 'translate-x-4' : ''}`}></div>
                </div>
                <span className="mr-3 text-sm font-bold text-slate-300 group-hover:text-white transition-colors">تذكرني على هذا الجهاز</span>
              </label>
            </div>

            <div className="pt-4 border-t border-slate-700/50">
              <p className="text-center text-[11px] text-slate-500 leading-relaxed font-medium">
                💡 نصيحة: إذا كنت تستخدم الموبايل، تأكد من فتح الموقع في متصفح خارجي (Chrome أو Safari) لضمان بقاء الجلسة نشطة دائماً.
              </p>
            </div>
          </div>
          
          <div className="mt-8 text-center pt-6 opacity-40">
            <p className="text-slate-500 text-[10px] font-bold uppercase tracking-widest">
              نظام لمّاح الذكي &copy; {new Date().getFullYear()}
            </p>
          </div>
        </div>
      </motion.div>
    </main>
  );
}
