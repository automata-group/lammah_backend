"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Phone, KeyRound, Loader2, BookOpen, ShieldCheck, ChevronDown } from "lucide-react";

const COUNTRY_CODES = [
  { code: "+970", flag: "🇵🇸", name: "فلسطين" },
  { code: "+972", flag: "🇮🇱", name: "إسرائيل" },
  { code: "+966", flag: "🇸🇦", name: "السعودية" },
  { code: "+971", flag: "🇦🇪", name: "الإمارات" },
  { code: "+962", flag: "🇯🇴", name: "الأردن" },
  { code: "+20", flag: "🇪🇬", name: "مصر" },
  { code: "+961", flag: "🇱🇧", name: "لبنان" },
  { code: "+964", flag: "🇮🇶", name: "العراق" },
  { code: "+968", flag: "🇴🇲", name: "عمان" },
  { code: "+974", flag: "🇶🇦", name: "قطر" },
  { code: "+973", flag: "🇧🇭", name: "البحرين" },
  { code: "+965", flag: "🇰🇼", name: "الكويت" },
  { code: "+963", flag: "🇸🇾", name: "سوريا" },
  { code: "+967", flag: "🇾🇪", name: "اليمن" },
  { code: "+218", flag: "🇱🇾", name: "ليبيا" },
  { code: "+216", flag: "🇹🇳", name: "تونس" },
  { code: "+213", flag: "🇩🇿", name: "الجزائر" },
  { code: "+212", flag: "🇲🇦", name: "المغرب" },
  { code: "+249", flag: "🇸🇩", name: "السودان" },
  { code: "+252", flag: "🇸🇴", name: "الصومال" },
  { code: "+90", flag: "🇹🇷", name: "تركيا" },
  { code: "+98", flag: "🇮🇷", name: "إيران" },
  { code: "+92", flag: "🇵🇰", name: "باكستان" },
  { code: "+91", flag: "🇮🇳", name: "الهند" },
  { code: "+60", flag: "🇲🇾", name: "ماليزيا" },
  { code: "+62", flag: "🇮🇩", name: "إندونيسيا" },
  { code: "+1", flag: "🇺🇸", name: "أمريكا" },
  { code: "+44", flag: "🇬🇧", name: "بريطانيا" },
  { code: "+49", flag: "🇩🇪", name: "ألمانيا" },
  { code: "+33", flag: "🇫🇷", name: "فرنسا" },
  { code: "+34", flag: "🇪🇸", name: "إسبانيا" },
  { code: "+39", flag: "🇮🇹", name: "إيطاليا" },
  { code: "+31", flag: "🇳🇱", name: "هولندا" },
  { code: "+46", flag: "🇸🇪", name: "السويد" },
  { code: "+47", flag: "🇳🇴", name: "النرويج" },
  { code: "+45", flag: "🇩🇰", name: "الدنمارك" },
  { code: "+41", flag: "🇨🇭", name: "سويسرا" },
  { code: "+43", flag: "🇦🇹", name: "النمسا" },
  { code: "+32", flag: "🇧🇪", name: "بلجيكا" },
  { code: "+48", flag: "🇵🇱", name: "بولندا" },
  { code: "+7", flag: "🇷🇺", name: "روسيا" },
  { code: "+380", flag: "🇺🇦", name: "أوكرانيا" },
  { code: "+86", flag: "🇨🇳", name: "الصين" },
  { code: "+81", flag: "🇯🇵", name: "اليابان" },
  { code: "+82", flag: "🇰🇷", name: "كوريا الجنوبية" },
  { code: "+61", flag: "🇦🇺", name: "أستراليا" },
  { code: "+64", flag: "🇳🇿", name: "نيوزيلندا" },
  { code: "+55", flag: "🇧🇷", name: "البرازيل" },
  { code: "+52", flag: "🇲🇽", name: "المكسيك" },
  { code: "+27", flag: "🇿🇦", name: "جنوب أفريقيا" },
  { code: "+234", flag: "🇳🇬", name: "نيجيريا" },
  { code: "+254", flag: "🇰🇪", name: "كينيا" },
  { code: "+251", flag: "🇪🇹", name: "إثيوبيا" },
  { code: "+233", flag: "🇬🇭", name: "غانا" },
  { code: "+63", flag: "🇵🇭", name: "الفلبين" },
  { code: "+66", flag: "🇹🇭", name: "تايلاند" },
  { code: "+84", flag: "🇻🇳", name: "فيتنام" },
  { code: "+65", flag: "🇸🇬", name: "سنغافورة" },
  { code: "+94", flag: "🇱🇰", name: "سريلانكا" },
  { code: "+880", flag: "🇧🇩", name: "بنغلاديش" },
  { code: "+977", flag: "🇳🇵", name: "نيبال" },
  { code: "+993", flag: "🇹🇲", name: "تركمانستان" },
  { code: "+998", flag: "🇺🇿", name: "أوزبكستان" },
];

export default function LoginPage() {
  const { user, loading, signInWithOtp, verifyOtp } = useAuth();
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [countryCode, setCountryCode] = useState("+970");
  const [showCountries, setShowCountries] = useState(false);
  const [search, setSearch] = useState("");
  const [otp, setOtp] = useState("");
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [fullPhone, setFullPhone] = useState("");

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [user, loading]);

  const selectedCountry = COUNTRY_CODES.find(c => c.code === countryCode) || COUNTRY_CODES[0];

  const filteredCountries = COUNTRY_CODES.filter(c =>
    c.name.includes(search) || c.code.includes(search)
  );

  const handleSendOtp = async () => {
    const cleaned = phone.replace(/\s/g, "").replace(/^0+/, "");
    if (!cleaned || cleaned.length < 7) { setError("يرجى إدخال رقم هاتف صحيح."); return; }
    
    const formatted = `${countryCode}${cleaned}`;
    setSubmitting(true); setError("");
    
    const { error } = await signInWithOtp(formatted);
    setSubmitting(false);
    if (error) { setError(error.message || "فشل في إرسال رمز التحقق."); return; }
    
    setFullPhone(formatted);
    setStep("otp");
  };

  const handleVerifyOtp = async () => {
    if (otp.length < 6) { setError("الرمز يجب أن يكون 6 أرقام."); return; }
    setSubmitting(true); setError("");
    const { error } = await verifyOtp(fullPhone, otp);
    setSubmitting(false);
    if (error) { setError(error.message || "رمز التحقق غير صحيح."); return; }
    router.replace("/dashboard");
  };

  if (loading) return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center">
      <Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />
    </div>
  );

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 flex flex-col items-center justify-center p-8" dir="rtl">
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-md">
        
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-20 h-20 bg-indigo-500/20 rounded-full mb-6 ring-2 ring-indigo-400/30">
            <BookOpen className="w-10 h-10 text-indigo-400" />
          </div>
          <h1 className="text-3xl font-extrabold mb-3 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">
            المنصة الذكية للطلاب
          </h1>
          <p className="text-slate-400 text-sm">سجل دخولك برقم الهاتف للبدء</p>
        </div>

        <div className="bg-slate-800/60 border border-slate-700/50 rounded-3xl p-8 backdrop-blur-xl shadow-2xl">
          <div className="flex items-center mb-6 text-indigo-300">
            <ShieldCheck className="w-5 h-5 ml-2" />
            <span className="text-sm font-medium">تسجيل دخول آمن بالهاتف (OTP)</span>
          </div>

          {step === "phone" ? (
            <div className="space-y-5">
              <div>
                <label className="block text-sm text-slate-400 mb-2">رقم الهاتف</label>
                <div className="flex gap-2" dir="ltr">
                  {/* Country Selector */}
                  <div className="relative">
                    <button onClick={() => setShowCountries(!showCountries)} type="button"
                      className="flex items-center gap-1.5 bg-slate-900/80 border border-slate-700 rounded-xl py-4 px-3 text-slate-100 hover:border-indigo-500 transition-all min-w-[110px]">
                      <span className="text-xl">{selectedCountry.flag}</span>
                      <span className="text-sm font-mono">{countryCode}</span>
                      <ChevronDown className="w-3.5 h-3.5 text-slate-500" />
                    </button>

                    {showCountries && (
                      <div className="absolute top-full mt-2 left-0 w-72 max-h-80 overflow-hidden bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl z-50">
                        <div className="p-3 border-b border-slate-700">
                          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                            placeholder="ابحث عن دولة..."
                            className="w-full bg-slate-900/80 border border-slate-700 rounded-lg py-2 px-3 text-sm text-slate-100 placeholder:text-slate-500 outline-none focus:border-indigo-500" dir="rtl" autoFocus />
                        </div>
                        <div className="max-h-60 overflow-y-auto">
                          {filteredCountries.map((c) => (
                            <button key={c.code + c.name} type="button"
                              onClick={() => { setCountryCode(c.code); setShowCountries(false); setSearch(""); }}
                              className={`w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-700/50 transition-colors text-left ${countryCode === c.code ? "bg-indigo-500/10 text-indigo-300" : ""}`}>
                              <span className="text-xl">{c.flag}</span>
                              <span className="text-sm flex-1" dir="rtl">{c.name}</span>
                              <span className="text-xs font-mono text-slate-500">{c.code}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Phone Input */}
                  <div className="relative flex-1">
                    <input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
                      placeholder="5XX XXX XXXX"
                      className="w-full bg-slate-900/80 border border-slate-700 rounded-xl py-4 px-4 text-slate-100 placeholder:text-slate-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none transition-all" />
                  </div>
                </div>
              </div>

              {error && <p className="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg p-3">{error}</p>}

              <button onClick={handleSendOtp} disabled={submitting}
                className="w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-bold py-4 rounded-xl shadow-lg hover:shadow-indigo-500/30 transition-all disabled:opacity-50">
                {submitting ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "إرسال رمز التحقق"}
              </button>
            </div>
          ) : (
            <div className="space-y-5">
              <p className="text-sm text-slate-400 text-center bg-slate-900/50 rounded-xl p-3 border border-slate-700">
                تم إرسال رمز التحقق إلى <span className="text-indigo-300 font-mono" dir="ltr">{fullPhone}</span>
              </p>

              <div>
                <label className="block text-sm text-slate-400 mb-2">رمز التحقق (6 أرقام)</label>
                <div className="relative">
                  <KeyRound className="absolute right-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500" />
                  <input type="text" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    placeholder="000000" maxLength={6}
                    className="w-full bg-slate-900/80 border border-slate-700 rounded-xl py-4 pr-12 pl-4 text-slate-100 text-center font-mono text-2xl tracking-[0.5em] placeholder:text-slate-600 focus:border-indigo-500 outline-none transition-all" dir="ltr" />
                </div>
              </div>

              {error && <p className="text-red-400 text-sm bg-red-500/10 border border-red-500/30 rounded-lg p-3">{error}</p>}

              <button onClick={handleVerifyOtp} disabled={submitting}
                className="w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-bold py-4 rounded-xl shadow-lg hover:shadow-indigo-500/30 transition-all disabled:opacity-50">
                {submitting ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "تأكيد الدخول"}
              </button>

              <button onClick={() => { setStep("phone"); setOtp(""); setError(""); }}
                className="w-full text-slate-400 text-sm hover:text-indigo-300 transition-colors">
                ← تغيير رقم الهاتف
              </button>
            </div>
          )}
        </div>
      </motion.div>

      {/* Click outside to close dropdown */}
      {showCountries && <div className="fixed inset-0 z-40" onClick={() => setShowCountries(false)} />}
    </main>
  );
}
