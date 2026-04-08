"use client";

import { useState, useRef, useEffect } from "react";
import { useAuth } from "@/context/AuthContext";
import { useRouter, useSearchParams } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, FileAudio, FileText, CheckCircle, Loader2, BookOpen, AlertCircle, Quote, Eye, Clock, User, Package, History, LogOut, Target, Lightbulb, BrainCircuit, ListTodo, ChevronLeft, Check, X, MessageCircle, Send, Lock, Crown } from "lucide-react";

export default function DashboardPage() {
  const { user, session, loading, signOut } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const historyIdRaw = searchParams.get("history_id");
  const [currentHistoryId, setCurrentHistoryId] = useState<string | null>(historyIdRaw);

  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [queuePosition, setQueuePosition] = useState(0);
  const [progressStep, setProgressStep] = useState("");
  const [quota, setQuota] = useState<number | null>(null);
  const [timeLeft, setTimeLeft] = useState<number | null>(null);
  const [planName, setPlanName] = useState<string>("مجاني");

  const isBasicPlan = planName.includes("طالب") || planName.includes("أساسي") || planName === "مجاني";

  // UI States for Expansion
  const [activeTab, setActiveTab] = useState("notes");
  const [flippedCards, setFlippedCards] = useState<Record<number, boolean>>({});
  const [selectedAnswers, setSelectedAnswers] = useState<Record<number, string>>({});
  const [showExplanation, setShowExplanation] = useState<Record<number, boolean>>({});

  // Chat States
  const [chatMessages, setChatMessages] = useState<Array<{role: string, content: string}>>([]);
  const [chatInput, setChatInput] = useState("");
  const [isChatLoading, setIsChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [pdfPage, setPdfPage] = useState<number | null>(null);

  const summaryRef = useRef<HTMLDivElement>(null);

  // Progress Countdown Timer
  useEffect(() => {
    if (timeLeft !== null && timeLeft > 0) {
      const timer = setInterval(() => {
        setTimeLeft(prev => prev && prev > 0 ? prev - 1 : 0);
      }, 1000);
      return () => clearInterval(timer);
    }
  }, [timeLeft]);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading]);

  useEffect(() => {
    if (typeof window !== "undefined" && session) {
      const params = new URLSearchParams(window.location.search);
      const historyId = params.get("history_id");
      
      if (historyId) {
        setIsProcessing(true);
        setProgressStep("جاري استرجاع المحاضرة والتظليلات من الأرشيف...");
        
        // Remove query param to prevent loop on subsequent re-renders
        window.history.replaceState({}, document.title, window.location.pathname);
        setCurrentHistoryId(historyId);

        fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/subscription/history/${historyId}`, {
          headers: { "Authorization": `Bearer ${session.access_token}` }
        })
        .then(async r => {
          if (!r.ok) throw new Error("التلخيص غير موجود أو محذوف");
          const data = await r.json();
          if (data?.metadata?.job_id) {
            setResult({
              transcript: data.metadata.transcript || "",
              downloadUrl: `/api/v1/summarize/download/${data.metadata.job_id}.pdf`,
              annotations: data.metadata.annotations
            });
          } else {
            throw new Error("هذا التلخيص قديم جداً ولا يحتوي على أرشيف محفوظ.");
          }
        })
        .catch(err => setError(err.message))
        .finally(() => {
          setIsProcessing(false);
          setProgressStep("");
        });
      }
    }
  }, [session]);

  // Fetch quota on load - SHOW ERRORS
  useEffect(() => {
    if (session) {
      fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/subscription/quota`, {
        headers: { "Authorization": `Bearer ${session.access_token}` }
      }).then(async r => {
        const data = await r.json();
        if (!r.ok) {
          const msg = data.detail?.user_message || data.detail?.technical || JSON.stringify(data);
          setError(`⚠️ خطأ من الخادم: ${msg}`);
          console.error("Backend error:", data);
        } else {
          setQuota(data.quota_remaining);
          setPlanName(data.plan_name);
          setError(null);
        }
      }).catch(e => {
        setError(`❌ فشل الاتصال بالخادم: ${e.message}`);
        console.error("Network error:", e);
      });
    }
  }, [session, result]);

  const handleProcess = async () => {
    if (!audioFile) { setError("يرجى إرفاق الملف الصوتي أولاً."); return; }
    if (!session) { router.replace("/login"); return; }
    
    // -- LOCAL FINGERPRINT CHECK FOR FREE TRIALS --
    const isFreeTrial = planName === "مجاني";
    if (isFreeTrial) {
      const trialUsed = localStorage.getItem("device_trial_used");
      if (trialUsed === "true") {
        setError("عذراً! لقد استهلكت بالفعل المحاضرة المجانية التجريبية الخاصة بهذا الجهاز. لدعم المنصة والاستمرار بخدمتك المتميزة، يرجى الاشتراك في إحدى الباقات 🚀");
        return;
      }
    }

    setIsProcessing(true); setError(null); setResult(null);
    setIsStreaming(false); setQueuePosition(0); setProgressStep("");

    const formData = new FormData();
    formData.append("audio_file", audioFile);
    if (pdfFile) formData.append("pdf_file", pdfFile);

    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/summarize/process-lecture`, {
        method: "POST", body: formData,
        headers: { "Authorization": `Bearer ${session.access_token}` },
      });

      if (!response.ok) {
        try {
          const errData = await response.json();
          const userMsg = errData.detail?.user_message;
          if (userMsg) {
            if (errData.detail?.type === "no_quota" || errData.detail?.type === "quota_exceeded") {
              setError(userMsg);
              setIsProcessing(false);
              return;
            }
            throw new Error(userMsg);
          }
          throw new Error(typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail));
        } catch (parseErr: any) { if (parseErr.message) throw parseErr; throw new Error("خطأ غير متوقع."); }
      }

      // -- SET LOCAL FINGERPRINT ON SUCCESS --
      if (isFreeTrial) {
        localStorage.setItem("device_trial_used", "true");
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder("utf-8");
      
      let streamBuffer = "";
      let metadata: any = null;
      if (!reader) throw new Error("فشل في فتح البث.");

      setIsStreaming(true); setIsProcessing(false);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        streamBuffer += decoder.decode(value, { stream: true });
        
        let boundary = streamBuffer.indexOf('\n\n');
        while (boundary !== -1) {
          const rawEvent = streamBuffer.slice(0, boundary);
          streamBuffer = streamBuffer.slice(boundary + 2);
          
          for (const line of rawEvent.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const p = JSON.parse(line.slice(6));
              switch (p.type) {
                case "queued": setQueuePosition(p.position); setProgressStep(`📋 الطابور: #${p.position}`); break;
                case "queue_update": setQueuePosition(p.position); setProgressStep(`📋 الطابور: #${p.position}`); break;
                case "processing": case "progress": 
                  setQueuePosition(0); 
                  setProgressStep(`${p.message}`); 
                  if (p.time_estimate) setTimeLeft(p.time_estimate);
                  break;
                case "metadata": 
                  metadata = p; 
                  setActiveTab("notes");
                  setFlippedCards({});
                  setSelectedAnswers({});
                  setShowExplanation({});
                  break;
                case "done":
                  setResult({ transcript: metadata?.transcript || "", downloadUrl: p.download_url, annotations: metadata?.annotations });
                  if (p.history_id) {
                    setCurrentHistoryId(p.history_id);
                  }
                  setIsStreaming(false); setProgressStep(""); break;
                case "error": setError(p.message); setIsStreaming(false); break;
              }
            } catch {}
          }
          boundary = streamBuffer.indexOf('\n\n');
        }
      }
    } catch (err: any) { setError(err.message); setIsProcessing(false); setIsStreaming(false); }
  };

  const handleChatSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || isChatLoading || !currentHistoryId || !session) return;
    
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setIsChatLoading(true);

    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/chat/`, {
        method: "POST",
        headers: { 
          "Authorization": `Bearer ${session.access_token}`,
          "Content-Type": "application/json" 
        },
        body: JSON.stringify({
          history_id: currentHistoryId,
          question: userMsg,
          chat_history: chatMessages
        }),
      });

      if (!response.ok) throw new Error("فشل الاتصال بالخادم");

      const reader = response.body?.getReader();
      const decoder = new TextDecoder("utf-8");
      if (!reader) throw new Error("فشل فتح البث");

      setChatMessages(prev => [...prev, { role: "assistant", content: "" }]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value, { stream: true });
        setChatMessages(prev => {
          const newMessages = [...prev];
          const lastIndex = newMessages.length - 1;
          newMessages[lastIndex] = { ...newMessages[lastIndex], content: newMessages[lastIndex].content + chunk };
          return newMessages;
        });
      }
    } catch (err: any) {
      setChatMessages(prev => [...prev, { role: "assistant", content: "❌ عذراً، حدث خطأ: " + err.message }]);
    } finally {
      setIsChatLoading(false);
    }
  };

  if (loading || !user) return <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  const showResults = !!result;

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 text-slate-100 flex flex-col" dir="rtl">
      {/* Top Nav */}
      <nav className="sticky top-0 z-50 bg-slate-900/80 backdrop-blur-xl border-b border-slate-800 px-6 py-3">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className="flex items-center">
            <img src="/lammah-logo.png" alt="Lammah" className="w-8 h-8 ml-2 object-contain drop-shadow-md" />
            <span className="font-bold text-lg bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 to-purple-300">لمّاح</span>
          </div>
          <div className="flex items-center gap-3">
            {quota !== null && (
              <div className="bg-indigo-500/10 border border-indigo-500/30 rounded-full px-4 py-1.5 text-sm">
                <span className="text-slate-400">متبقي: </span>
                <span className={`font-bold ${quota > 0 ? "text-green-400" : "text-red-400"}`}>{quota}</span>
                <span className="text-slate-500 text-xs mr-1">محاضرة</span>
              </div>
            )}
            <button onClick={() => router.push("/plans")} className="text-slate-400 hover:text-indigo-300 transition-colors p-2" title="الباقات"><Package className="w-5 h-5" /></button>
            <button onClick={() => router.push("/history")} className="text-slate-400 hover:text-indigo-300 transition-colors p-2" title="السجل"><History className="w-5 h-5" /></button>
            <button onClick={() => router.push("/profile")} className="text-slate-400 hover:text-indigo-300 transition-colors p-2" title="حسابي"><User className="w-5 h-5" /></button>
            <button onClick={async () => { await signOut(); router.replace("/login"); }} className="text-slate-400 hover:text-red-300 transition-colors p-2" title="خروج"><LogOut className="w-5 h-5" /></button>
          </div>
        </div>
      </nav>

      <div className="flex-1 flex flex-col items-center p-8 overflow-x-hidden relative">
        {/* Full Page Processing Overlay */}
        <AnimatePresence>
          {(isProcessing || isStreaming) && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/90 backdrop-blur-xl p-4">
              <div className="bg-slate-900 border border-indigo-500/30 rounded-[2.5rem] p-10 max-w-lg w-full shadow-[0_0_80px_rgba(99,102,241,0.2)] text-center relative overflow-hidden">
                {/* Decorative background gradients */}
                <div className="absolute top-0 left-0 w-full h-1/2 bg-gradient-to-b from-indigo-500/10 to-transparent pointer-events-none"></div>
                
                <div className="mx-auto w-24 h-24 mb-8 relative">
                  <div className="absolute inset-0 border-4 border-slate-700/50 rounded-full"></div>
                  <div className="absolute inset-0 border-4 border-indigo-500 rounded-full border-t-transparent animate-spin"></div>
                  {queuePosition > 0 ? (
                    <Clock className="w-10 h-10 text-indigo-400 absolute inset-0 m-auto animate-pulse" />
                  ) : (
                    <Loader2 className="w-10 h-10 text-indigo-400 absolute inset-0 m-auto animate-[spin_2s_linear_infinite]" />
                  )}
                </div>
                
                <h3 className="text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-l from-indigo-300 to-purple-400 mb-4">
                  {queuePosition > 0 ? "أنت في قائمة الانتظار" : "المحرك الذكي يعمل ✨"}
                </h3>
                
                <p className="text-lg text-slate-300 leading-relaxed font-medium mb-4">
                  {queuePosition > 0 
                    ? `دورك في الطابور: ${queuePosition}` 
                    : (progressStep || "جاري الاتصال والتحضير...")}
                </p>
                
                {timeLeft !== null && progressStep && queuePosition === 0 && (
                  <div className="flex justify-center items-center gap-2 mb-8 bg-indigo-500/10 py-2 px-6 rounded-full w-max mx-auto border border-indigo-500/20">
                    <Clock className="w-4 h-4 text-indigo-400 animate-pulse" />
                    <span className="text-sm font-bold text-indigo-300">
                      الوقت المتبقي تقريباً: {Math.floor(timeLeft / 60)}:{(timeLeft % 60).toString().padStart(2, '0')}
                    </span>
                  </div>
                )}
                {!timeLeft && progressStep && queuePosition === 0 && (
                  <div className="mb-8 h-8"></div>
                )}
                
                {/* Progress Bar Container */}
                <div className="w-full bg-slate-950 rounded-full h-3 border border-slate-800 overflow-hidden p-0.5">
                  <motion.div className="bg-gradient-to-r from-indigo-500 to-purple-500 h-full rounded-full relative overflow-hidden"
                    animate={{ width: queuePosition > 0 ? "5%" : progressStep.includes("5/5") ? "95%" : progressStep.includes("4/5") ? "75%" : progressStep.includes("3/5") ? "55%" : progressStep.includes("2/5") ? "35%" : progressStep.includes("1/5") ? "15%" : "5%" }}
                    transition={{ duration: 0.8, ease: "easeOut" }} 
                  >
                  </motion.div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Header */}
        {!showResults && (
          <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-4xl text-center mb-12 mt-6">
            <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight mb-4 bg-clip-text text-transparent bg-gradient-to-l from-indigo-300 via-purple-300 to-indigo-100 leading-tight">
              حول محاضراتك إلى ملخصات ذكية
            </h1>
            <p className="text-lg text-slate-400 max-w-2xl mx-auto">ارفع التسجيل الصوتي واسمح لمحركنا الذكي باستخراج الشروحات والملاحظات بدقة عالية</p>
          </motion.div>
        )}

        {/* Upload */}
        {!showResults && (
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}
            className="w-full max-w-3xl bg-slate-800/40 border border-slate-700/50 rounded-3xl p-8 shadow-2xl relative overflow-hidden backdrop-blur-xl">
            <div className="grid md:grid-cols-2 gap-6 mb-8">
              <div className={`relative group border-2 border-dashed rounded-3xl p-8 flex flex-col items-center justify-center transition-all ${audioFile ? 'border-indigo-500 bg-indigo-500/10' : 'border-slate-600 hover:border-indigo-400 cursor-pointer'}`}>
                <input type="file" accept="audio/*" onChange={(e) => setAudioFile(e.target.files?.[0] || null)} className="absolute inset-0 opacity-0 cursor-pointer z-10" />
                {audioFile ? (<><CheckCircle className="w-14 h-14 text-indigo-400 mb-4" /><p className="font-semibold text-indigo-200 truncate w-full text-center">{audioFile.name}</p></>) : (<><FileAudio className="w-10 h-10 text-indigo-400 mb-3" /><p className="text-slate-300 font-medium">تسجيل المحاضرة</p><p className="text-xs text-slate-500">MP3, WAV, M4A</p></>)}
              </div>
              <div className={`relative group border-2 border-dashed rounded-3xl p-8 flex flex-col items-center justify-center transition-all ${pdfFile ? 'border-purple-500 bg-purple-500/10' : 'border-slate-600 hover:border-purple-400 cursor-pointer'}`}>
                <input type="file" accept={isBasicPlan ? "application/pdf" : "application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx"} onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file && isBasicPlan && file.name.endsWith('.pptx')) {
                     setError("رفع وتحويل ملفات الباوربوينت (PPTX) حصري للباقات المتقدمة و VIP 🎉 يرجى ترقية باقتك من صفحة الباقات.");
                     setPdfFile(null);
                     e.target.value = "";
                  } else {
                     setPdfFile(file || null);
                  }
                }} className="absolute inset-0 opacity-0 cursor-pointer z-10" />
                {pdfFile ? (<><CheckCircle className="w-14 h-14 text-purple-400 mb-4" /><p className="font-semibold text-purple-200 truncate w-full text-center" dir="ltr">{pdfFile.name}</p></>) : (<><FileText className="w-10 h-10 text-purple-400 mb-3" /><p className="text-slate-300 font-medium">شرائح PDF { !isBasicPlan && <span>أو PPTX</span> } (اختياري)</p>
                   {isBasicPlan ? (
                      <div className="flex items-center text-[11px] font-bold text-amber-400 mt-3 bg-amber-400/10 px-3 py-1.5 rounded-full border border-amber-500/20"><Lock className="w-3 h-3 ml-1.5" /> الـ PPTX المباشر متاح بالمتقدم</div>
                   ) : (
                      <p className="text-xs text-slate-500">لإنشاء ملزمة ملونة تفاعلية</p>
                   )}</>)}
              </div>
            </div>

            <AnimatePresence>
              {error && (
                <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="mb-6">
                  <div className="bg-red-500/10 border border-red-500/50 text-red-200 px-6 py-4 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium leading-relaxed">{error}</p>
                    </div>
                    <div className="flex flex-col sm:flex-row gap-2 shrink-0">
                      {error.includes("اشتراك") || error.includes("حصت") ? (
                        <button onClick={() => router.push("/plans")} className="text-sm bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg font-bold transition-colors min-w-[120px]">ترقية الباقة</button>
                      ) : null}
                      
                      <button 
                        onClick={() => {
                          const msg = `مرحباً بكِ مهندسة، أواجه مشكلة تقنية في المنصة.\nحسابي: ${user?.user_metadata?.full_name || user?.email || 'غير معروف'}\nالخطأ الذي ظهر لي:\n"${error}"\nأرجو المساعدة.`;
                          window.open(`https://wa.me/970598298930?text=${encodeURIComponent(msg)}`, '_blank');
                        }}
                        className="text-sm bg-slate-800 border border-slate-600 hover:bg-slate-700 text-slate-200 px-4 py-2 rounded-lg flex items-center justify-center gap-2 transition-colors min-w-[140px]"
                        title="إرسال سجل الخطأ للدعم الفني عبر واتساب"
                      >
                        <MessageCircle className="w-4 h-4 text-green-400" />
                        إرسال الخطأ للإدارة
                      </button>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <button onClick={handleProcess} disabled={isProcessing || !audioFile}
              className="w-full bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-bold py-5 rounded-2xl shadow-xl hover:shadow-indigo-500/30 transition-all disabled:opacity-50">
              {isProcessing ? <span className="flex items-center justify-center"><Loader2 className="w-6 h-6 ml-3 animate-spin" />جاري المعالجة...</span> : <span className="flex items-center justify-center"><Upload className="w-6 h-6 ml-3" />ابدأ التلخيص الذكي</span>}
            </button>
          </motion.div>
        )}

        {/* Results */}
        <AnimatePresence>
          {showResults && (
            <motion.div initial={{ opacity: 0, y: 50 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-6xl grid grid-cols-1 lg:grid-cols-3 gap-8 pb-20">
              <div className="lg:col-span-2 space-y-8">
                {result?.downloadUrl && (
                  <div className="bg-slate-800/60 border border-indigo-500/50 p-6 rounded-[2rem] shadow-2xl relative">
                    <div className="flex justify-between items-center mb-6">
                      <h2 className="text-2xl font-bold flex items-center text-indigo-300">
                        <BookOpen className="w-7 h-7 ml-3 text-indigo-400" />الملزمة الذكية
                      </h2>
                      <a href={`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}${result.downloadUrl}`} download target="_blank"
                         className="bg-gradient-to-l from-indigo-600 to-purple-600 hover:opacity-90 text-white px-6 py-2.5 rounded-xl font-bold transition flex items-center shadow-lg">
                         تحميل الملف المخطط الذكي
                      </a>
                    </div>
                    <iframe 
                       key={pdfPage || 'default'}
                       src={`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}${result.downloadUrl}${pdfPage ? `#page=${pdfPage}` : ''}`} 
                       className="w-full h-[600px] rounded-2xl border border-slate-700 bg-white transition-all" 
                    />
                  </div>
                )}
                
                {!result?.downloadUrl && result?.transcript && (
                  <div className="bg-slate-800/60 p-8 rounded-3xl border border-slate-700 text-center text-slate-400">
                     <AlertCircle className="w-12 h-12 mx-auto mb-4 text-slate-500" />
                     <p>لم يتم إرفاق ملف PDF أو تعذر التعديل عليه. يمكنك مراجعة النص المفرغ فقط.</p>
                  </div>
                )}
              </div>
              <div className="space-y-6">
                
                {/* TABS HEADERS */}
                {(result?.annotations?.sticky_notes?.length > 0 || result?.annotations?.focus_points?.length > 0 || result?.annotations?.mcqs?.length > 0 || currentHistoryId) && (
                  <div className="flex flex-wrap gap-2 space-x-reverse bg-slate-800/80 p-1.5 rounded-2xl border border-slate-700 shadow-xl backdrop-blur-md">
                    <button onClick={() => setActiveTab("notes")} className={`flex-1 min-w-[100px] flex items-center justify-center py-3 rounded-xl font-bold transition-all ${activeTab === "notes" ? "bg-indigo-600 shadow-md text-white scale-[0.98]" : "text-slate-400 hover:text-indigo-300 hover:bg-slate-700/50"}`}>
                      <BookOpen className="w-5 h-5 ml-2" />الشروحات
                    </button>
                    {(result?.annotations?.focus_points?.length > 0) && (
                      <button onClick={() => setActiveTab("focus")} className={`flex-1 min-w-[100px] flex items-center justify-center py-3 rounded-xl font-bold transition-all ${activeTab === "focus" ? "bg-amber-600 shadow-md text-white scale-[0.98]" : "text-slate-400 hover:text-amber-300 hover:bg-slate-700/50"}`}>
                        <Target className="w-5 h-5 ml-2" />التركيز
                      </button>
                    )}
                    {(result?.annotations?.mcqs?.length > 0 || result?.annotations?.flashcards?.length > 0) && (
                      <button onClick={() => setActiveTab("quiz")} className={`flex-1 min-w-[100px] flex items-center justify-center py-3 rounded-xl font-bold transition-all ${activeTab === "quiz" ? "bg-emerald-600 shadow-md text-white scale-[0.98]" : "text-slate-400 hover:text-emerald-300 hover:bg-slate-700/50"}`}>
                        <BrainCircuit className="w-5 h-5 ml-2" />الأسئلة
                      </button>
                    )}
                    {currentHistoryId && (
                      <button onClick={() => setActiveTab("chat")} className={`flex-1 min-w-[100px] flex items-center justify-center py-3 rounded-xl font-bold transition-all ${activeTab === "chat" ? "bg-blue-600 shadow-md text-white scale-[0.98]" : "text-slate-400 hover:text-blue-300 hover:bg-slate-700/50"}`}>
                        <MessageCircle className="w-5 h-5 ml-2" />تحدث
                      </button>
                    )}
                  </div>
                )}

                {/* TAB CONTENT: NOTES */}
                <AnimatePresence mode="wait">
                  {activeTab === "notes" && (
                    <motion.div key="notes" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="space-y-6">
                      {result?.annotations?.sticky_notes?.length > 0 && (
                        <div className="bg-gradient-to-b from-indigo-900/30 to-slate-800/40 border border-indigo-500/20 p-6 rounded-[2rem] shadow-xl relative overflow-hidden">
                          <h3 className="text-xl font-bold flex items-center mb-6 text-indigo-300">
                            ملاحظات المحاضر السريعة
                          </h3>
                          <div className="space-y-4 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                            {result.annotations.sticky_notes.map((note: any, i: number) => (
                              <div key={i} onClick={() => note.page_number && setPdfPage(note.page_number)} className="bg-slate-900/80 p-5 rounded-2xl border border-slate-700/50 cursor-pointer hover:bg-slate-800 transition-colors">
                                <span className="text-xs font-bold bg-indigo-500/20 text-indigo-300 px-3 py-1 rounded-full mb-3 inline-block">شريحة {note.page_number}</span>
                                <p className="text-sm text-slate-300 leading-relaxed">{note.note_text}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {result?.annotations?.slide_explanations?.length > 0 && (
                        <div className="bg-gradient-to-br from-indigo-900/30 to-purple-900/10 border border-purple-500/30 p-6 rounded-[2rem] shadow-xl relative overflow-hidden">
                          <h3 className="text-xl font-bold flex items-center mb-6 text-purple-400">
                            <Lightbulb className="w-6 h-6 ml-2 text-purple-500" />
                            الشرح الكافي للشرائح
                          </h3>
                          <div className="space-y-4 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
                            {result.annotations.slide_explanations.map((exp: any, i: number) => (
                              <div key={i} onClick={() => exp.page_number && setPdfPage(exp.page_number)} className="bg-slate-900/60 p-5 rounded-2xl border border-purple-500/20 cursor-pointer hover:bg-slate-800 transition-colors">
                                <span className="text-xs font-bold bg-purple-500/20 text-purple-300 px-3 py-1 rounded-full mb-3 inline-block">شريحة {exp.page_number}</span>
                                <p className="text-sm text-slate-300 leading-relaxed font-medium">{exp.explanation}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </motion.div>
                  )}

                  {/* TAB CONTENT: FOCUS */}
                  {activeTab === "focus" && (
                    <motion.div key="focus" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="space-y-6">
                      {result?.annotations?.focus_points?.length > 0 && (
                        <div className="bg-gradient-to-br from-amber-900/30 to-orange-900/10 border border-amber-500/30 p-6 rounded-[2rem] shadow-xl relative overflow-hidden">
                          <h3 className="text-xl font-bold flex items-center mb-6 text-amber-400">
                            <Target className="w-6 h-6 ml-2 text-amber-500" />
                            مواضع الأسئلة والتركيز
                          </h3>
                          <div className="space-y-4 max-h-[600px] overflow-y-auto pr-2 custom-scrollbar">
                            {result.annotations.focus_points.map((fp: any, i: number) => (
                              <div key={i} onClick={() => fp.page_number && setPdfPage(fp.page_number)} className="bg-slate-900/60 p-5 rounded-2xl border border-amber-500/20 border-r-4 border-r-amber-500 cursor-pointer hover:bg-slate-800 transition-colors">
                                <div className="flex justify-between items-start mb-2">
                                  <h4 className="text-base font-bold text-amber-300">{fp.point}</h4>
                                  {fp.page_number && <span className="text-[10px] font-bold bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full">شريحة {fp.page_number}</span>}
                                </div>
                                <p className="text-sm text-slate-300 leading-relaxed">{fp.explanation}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </motion.div>
                  )}

                  {/* TAB CONTENT: QUIZ */}
                  {activeTab === "quiz" && (
                    <motion.div key="quiz" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="space-y-6 relative">
                      {isBasicPlan && (
                        <div className="absolute inset-0 z-30 flex flex-col items-center justify-center bg-slate-900/60 backdrop-blur-md rounded-3xl p-8 text-center border border-amber-500/30 shadow-2xl">
                          <div className="w-20 h-20 bg-amber-500/20 rounded-full flex items-center justify-center mb-6 shadow-[0_0_50px_rgba(245,158,11,0.4)] border border-amber-400/50">
                            <Lock className="w-10 h-10 text-amber-400" />
                          </div>
                          <h3 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-l from-amber-200 to-orange-400 mb-4">طوّر دراستك واختبر نفسك!</h3>
                          <p className="text-lg text-slate-200 max-w-lg mb-8 leading-relaxed font-medium">
                            ميزة <strong>البطاقات التفاعلية (Flashcards)</strong> و <strong>الأسئلة المتوقعة (MCQs)</strong> حصرية لاشتراكات الباقات المتقدمة و الـ VIP. <br/><span className="text-slate-400 text-sm mt-2 block">رقي باقتك الآن لتفجير طاقتك الدراسية بأقوى أدوات الاسترجاع المتباعد!</span>
                          </p>
                          <button onClick={() => router.push("/plans")} className="bg-gradient-to-l from-amber-500 to-orange-600 hover:from-amber-400 hover:to-orange-500 text-slate-900 font-extrabold px-10 py-4 rounded-xl shadow-xl transition-all flex items-center text-lg">
                            <Crown className="w-6 h-6 ml-2" /> ترقية الباقة الآن لفك القفل
                          </button>
                        </div>
                      )}

                      <div className={isBasicPlan ? "opacity-20 pointer-events-none select-none blur-[6px] transition-all" : ""}>
                        {/* FLASHCARDS */}
                      {result?.annotations?.flashcards?.length > 0 && (
                        <div className="bg-gradient-to-br from-emerald-900/20 to-teal-900/10 border border-emerald-500/20 p-6 rounded-[2rem] shadow-xl relative">
                          <h3 className="text-xl font-bold flex items-center mb-5 text-emerald-400">
                            <BookOpen className="w-6 h-6 ml-2 text-emerald-500" />بطاقات الحفظ (Flashcards)
                          </h3>
                          <div className="grid grid-cols-1 gap-4">
                            {result.annotations.flashcards.map((fc: any, i: number) => (
                              <div key={i} 
                                onClick={() => setFlippedCards(prev => ({...prev, [i]: !prev[i]}))}
                                className="cursor-pointer min-h-[120px] relative group"
                                style={{ perspective: "1000px" }}
                              >
                                <motion.div 
                                  initial={false}
                                  animate={{ rotateY: flippedCards[i] ? 180 : 0 }}
                                  transition={{ duration: 0.6, type: "spring", bounce: 0.4 }}
                                  className="w-full h-full relative"
                                  style={{ transformStyle: "preserve-3d" }}
                                >
                                  {/* FRONT */}
                                  <div className="absolute inset-0 w-full flex items-center justify-center p-6 bg-slate-800 border-2 border-emerald-500/30 rounded-2xl shadow-lg group-hover:border-emerald-400 transition-colors" style={{ backfaceVisibility: "hidden" }}>
                                    <h4 className="text-lg font-bold text-emerald-300 text-center" dir="ltr">{fc.front}</h4>
                                    <div className="absolute bottom-3 text-xs text-slate-500 flex items-center"><ChevronLeft className="w-3 h-3 ml-1"/> انقر للقلب</div>
                                  </div>
                                  {/* BACK */}
                                  <div className="absolute inset-0 w-full flex items-center justify-center p-6 bg-emerald-900/80 border-2 border-emerald-400 rounded-2xl shadow-lg" style={{ transform: "rotateY(180deg)", backfaceVisibility: "hidden" }}>
                                    <p className="text-sm text-emerald-50 text-center leading-relaxed font-medium" dir="rtl">{fc.back}</p>
                                  </div>
                                </motion.div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* MCQs */}
                      {result?.annotations?.mcqs?.length > 0 && (
                        <div className="bg-gradient-to-br from-blue-900/20 to-cyan-900/10 border border-blue-500/20 p-6 rounded-[2rem] shadow-xl relative">
                          <h3 className="text-xl font-bold flex items-center mb-5 text-blue-400">
                            <ListTodo className="w-6 h-6 ml-2 text-blue-500" />اختبر نفسك (MCQs)
                          </h3>
                          <div className="space-y-6">
                            {result.annotations.mcqs.map((q: any, i: number) => {
                              const isAnswered = !!selectedAnswers[i];
                              const isCorrect = selectedAnswers[i] === q.correct_answer;
                              
                              return (
                                <div key={i} className="bg-slate-800/80 p-5 rounded-2xl border border-slate-700">
                                  <h4 className="text-base font-bold text-slate-200 mb-4">{i + 1}. {q.question}</h4>
                                  <div className="space-y-2">
                                    {q.options.map((opt: string, j: number) => {
                                      let btnClass = "bg-slate-900/50 border-slate-600 hover:border-blue-400 text-slate-300";
                                      if (isAnswered) {
                                        if (opt === q.correct_answer) btnClass = "bg-green-900/40 border-green-500 text-green-200";
                                        else if (opt === selectedAnswers[i]) btnClass = "bg-red-900/40 border-red-500 text-red-200";
                                        else btnClass = "bg-slate-900/20 border-slate-800 text-slate-600 opacity-50";
                                      }
                                      return (
                                        <button 
                                          key={j} 
                                          disabled={isAnswered}
                                          onClick={() => {
                                            setSelectedAnswers(prev => ({...prev, [i]: opt}));
                                            setShowExplanation(prev => ({...prev, [i]: true}));
                                          }}
                                          className={`w-full text-right p-4 rounded-xl border-2 transition-all flex items-center justify-between ${btnClass}`}
                                        >
                                          <span>{opt}</span>
                                          {isAnswered && opt === q.correct_answer && <Check className="w-5 h-5 text-green-400" />}
                                          {isAnswered && opt === selectedAnswers[i] && opt !== q.correct_answer && <X className="w-5 h-5 text-red-400" />}
                                        </button>
                                      );
                                    })}
                                  </div>
                                  <AnimatePresence>
                                    {showExplanation[i] && (
                                      <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} className="mt-4 overflow-hidden">
                                        <div className={`p-4 rounded-xl border ${isCorrect ? 'bg-green-900/20 border-green-500/30' : 'bg-amber-900/20 border-amber-500/30'}`}>
                                          <p className="text-sm font-medium text-slate-300">
                                            <span className={`font-bold ml-2 ${isCorrect ? 'text-green-400' : 'text-amber-400'}`}>السبب:</span> 
                                            {q.explanation}
                                          </p>
                                        </div>
                                      </motion.div>
                                    )}
                                  </AnimatePresence>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                      </div>
                    </motion.div>
                  )}

                  {/* TAB CONTENT: CHAT */}
                  {activeTab === "chat" && (
                    <motion.div key="chat" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95 }} className="flex flex-col h-[700px] bg-slate-800/80 border border-slate-700 rounded-[2rem] shadow-xl overflow-hidden relative">
                      <div className="bg-gradient-to-r from-blue-900/40 to-indigo-900/40 p-5 px-6 border-b border-slate-700/50 flex flex-col">
                        <h3 className="text-xl font-bold flex items-center text-blue-300">
                          <MessageCircle className="w-6 h-6 ml-2 text-blue-400" /> تحدث مع المحاضرة
                        </h3>
                        <p className="text-sm text-slate-400 pr-8 mt-1">يستند الرد لمعالجة سؤالك باحترافية، مع توضيح ما ذكره الدكتور في المحاضرة.</p>
                      </div>
                      
                      <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar bg-slate-900/50">
                        {chatMessages.length === 0 && (
                          <div className="h-full flex flex-col items-center justify-center text-slate-500 space-y-4 opacity-60">
                            <BrainCircuit className="w-16 h-16 text-slate-600" />
                            <p className="text-lg text-center">اطرح سؤالاً يخص جزئية معينة من المحاضرة وسيجيبك النظام فوراً!</p>
                          </div>
                        )}
                        {chatMessages.map((msg, idx) => (
                          <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} w-full`}>
                            <div className={`max-w-[85%] p-4 rounded-2xl ${msg.role === "user" ? "bg-blue-600/90 text-white rounded-tl-sm shadow-md" : "bg-slate-800 border border-slate-700 text-slate-200 rounded-tr-sm shadow-sm"}`}>
                              <p className="leading-relaxed whitespace-pre-wrap text-sm md:text-base">{msg.content}</p>
                            </div>
                          </div>
                        ))}
                        {isChatLoading && (
                          <div className="flex justify-start w-full">
                            <div className="max-w-[75%] p-4 rounded-2xl bg-slate-800 border border-slate-700 text-slate-400 rounded-tr-sm flex items-center space-x-2 space-x-reverse">
                              <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                              <span className="text-sm">يقرأ الشرائح والصوت...</span>
                            </div>
                          </div>
                        )}
                        <div ref={chatEndRef} />
                      </div>

                      <div className="p-4 bg-slate-800 border-t border-slate-700">
                        <form onSubmit={handleChatSubmit} className="flex space-x-2 space-x-reverse">
                          <input 
                            type="text" 
                            className="flex-1 bg-slate-900 border border-slate-700 text-slate-200 p-4 rounded-xl focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all placeholder-slate-500 shadow-inner"
                            placeholder="مثال: كيف شرح الدكتور الفرق بين DOS و DDOS؟"
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            disabled={isChatLoading}
                          />
                          <button 
                            type="submit" 
                            disabled={isChatLoading || !chatInput.trim()}
                            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:hover:bg-blue-600 text-white p-4 rounded-xl font-bold flex items-center justify-center transition-all shadow-md active:scale-95"
                          >
                            <Send className="w-5 h-5 rtl:-scale-x-100" />
                          </button>
                        </form>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
                
                {result && (
                  <button onClick={() => { setResult(null); setProgressStep(""); }}
                    className="w-full py-4 bg-slate-800/50 hover:bg-slate-800 border-2 border-slate-600/50 rounded-2xl text-slate-300 font-semibold transition-all shadow-xl">
                    محاضرة جديدة
                  </button>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Global Footer */}
      <footer className="w-full bg-slate-900 border-t border-slate-800 py-6 mt-auto">
        <div className="max-w-6xl mx-auto px-6 text-center flex flex-col items-center">
          <p className="text-slate-500 text-sm font-medium">
            جميع الحقوق محفوظة &copy; {new Date().getFullYear()} - تم التطوير والتصميم بواسطة <span className="text-indigo-400 font-bold hover:text-indigo-300 transition-colors cursor-default">فريق لمّاح</span>
          </p>
        </div>
      </footer>
    </main>
  );
}
