"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState, use, useRef } from "react";
import { createClient } from "@/lib/supabase";
import { motion } from "framer-motion";
import { Loader2, Copy, CheckCircle2, MessageCircle } from "lucide-react";

export default function CheckoutPage({ params }: { params: Promise<{ planId: string }> }) {
  const { planId } = use(params);
  const { user, session, loading } = useAuth();
  const router = useRouter();
  
  const [orderDetails, setOrderDetails] = useState<any>(null);
  const [checkoutLoading, setCheckoutLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [fullName, setFullName] = useState("");
  
  // Use a ref to prevent strict mode or session changes from creating multiple orders
  const hasCreatedOrder = useRef(false);

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
      return;
    }
    
    if (session && !hasCreatedOrder.current) {
      hasCreatedOrder.current = true;
      createOrder();
    }
  }, [user, loading, session]);
  
  const createOrder = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/checkout`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json", 
          "Authorization": `Bearer ${session?.access_token}` 
        },
        body: JSON.stringify({ plan_id: planId })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail?.user_message || "حدث خطأ أثناء إنشاء الطلب");
      
      setOrderDetails(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCheckoutLoading(false);
    }
  };
  
  const handleCopy = () => {
    if (orderDetails) {
      navigator.clipboard.writeText(orderDetails.payment_number);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };
  
  const handleWhatsApp = async () => {
    if (!orderDetails) return;
    if (!fullName.trim()) {
      alert("الرجاء إدخال اسمك الثلاثي لتسهيل المطابقة.");
      return;
    }
    try {
      if (session) {
        await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/update-name`, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json", 
            "Authorization": `Bearer ${session.access_token}` 
          },
          body: JSON.stringify({ full_name: fullName.trim() })
        });
      }
    } catch(e) {
      console.error("Failed to update name via API", e);
    }
    
    const phone = "970598298930"; // Formatted internationally
    const message = `مرحباً، أود تأكيد الدفع لباقة (${orderDetails.plan_name}).\nالاسم الثلاثي: ${fullName}\nرقم الطلب الخاص بي هو: ${orderDetails.order_id}\nمرفق صورة إيصال الدفع عبر تطبيق ريفلكت.`;
    const url = `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
    window.open(url, "_blank");
  };

  const handleOtherPayment = async () => {
    if (!orderDetails) return;
    if (!fullName.trim()) {
      alert("الرجاء إدخال اسمك الثلاثي أولاً.");
      return;
    }
    try {
      if (session) {
        await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/update-name`, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json", 
            "Authorization": `Bearer ${session.access_token}` 
          },
          body: JSON.stringify({ full_name: fullName.trim() })
        });
      }
    } catch(e) {
      console.error("Failed to update name via API", e);
    }
    
    const phone = "970598298930";
    const message = `مرحباً، أود الاشتراك في باقة (${orderDetails.plan_name}).\nالاسم الثلاثي: ${fullName}\nرقم الطلب: ${orderDetails.order_id}\nأريد الاستفسار عن طريقة دفع أخرى غير ريفلكت.`;
    const url = `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
    window.open(url, "_blank");
  };

  if (loading || checkoutLoading) return <div className="min-h-screen bg-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 p-8 flex justify-center items-center" dir="rtl">
      <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="max-w-md w-full bg-slate-800 rounded-3xl p-8 border border-slate-700 shadow-xl relative">
        <button onClick={() => router.push("/plans")} className="absolute top-6 right-6 text-slate-400 hover:text-indigo-300 transition-colors" title="العودة للباقات">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        </button>

        {error ? (
          <div className="text-center mt-6">
            <div className="text-red-400 mb-4">{error}</div>
            <button onClick={() => router.push("/plans")} className="text-indigo-400 hover:underline">العودة للباقات</button>
          </div>
        ) : orderDetails ? (
           <div className="text-center mt-6">
             <h1 className="text-2xl font-bold mb-6 text-white">إتمام الدفع اليدوي</h1>
             <p className="text-slate-400 text-sm mb-6">يرجى تحويل مبلغ <span className="text-indigo-400 font-bold">{orderDetails.amount} شيكل</span> عبر تطبيق ريفلكت (Reflect) إلى الرقم التالي:</p>
             
             <div className="bg-slate-900 rounded-xl p-4 flex justify-between items-center mb-6 border border-slate-700">
               <span className="text-2xl font-mono tracking-widest text-white">{orderDetails.payment_number}</span>
               <button onClick={handleCopy} className="text-slate-400 hover:text-white transition-colors">
                 {copied ? <CheckCircle2 className="text-green-400 w-6 h-6" /> : <Copy className="w-6 h-6" />}
               </button>
             </div>
             
             <div className="bg-indigo-900/30 border border-indigo-500/30 rounded-xl p-4 mb-6">
               <p className="text-sm text-indigo-200 mb-1">رقم الطلب الخاص بك</p>
               <p className="text-xl font-bold font-mono text-indigo-400">{orderDetails.order_id}</p>
             </div>

             <div className="mb-6 text-right">
               <label className="block text-sm text-slate-300 mb-2">اسمك الثلاثي (إجباري للمطابقة):</label>
               <input 
                 type="text" 
                 required
                 placeholder="الرجاء إدخال اسمك الثلاثي لمطابقته مع حوالة Reflect" 
                 value={fullName}
                 onChange={(e) => setFullName(e.target.value)}
                 className="w-full bg-slate-900/80 border border-slate-700 focus:border-indigo-500 rounded-xl py-3 px-4 text-slate-100 placeholder:text-slate-500 text-sm outline-none transition-all"
               />
             </div>
             
             <button 
               onClick={handleWhatsApp} 
               disabled={!fullName.trim()}
               className="w-full bg-green-600 hover:bg-green-500 text-white font-bold py-4 rounded-xl flex items-center justify-center gap-2 transition-all shadow-lg shadow-green-600/20 disabled:opacity-50 disabled:cursor-not-allowed mb-4"
               title={!fullName.trim() ? "الرجاء كتابة اسمك أولاً" : ""}
             >
               <MessageCircle className="w-5 h-5" />
               إرسال الإيصال عبر واتساب
             </button>

             <div className="bg-slate-900/40 border border-slate-700/50 rounded-xl p-5 mb-4 text-center">
               <p className="text-sm text-slate-400 mb-3">هل تفضل استخدام طريقة دفع أخرى؟</p>
               <button 
                 onClick={handleOtherPayment} 
                 disabled={!fullName.trim()} 
                 title={!fullName.trim() ? "الرجاء كتابة اسمك أولاً" : ""}
                 className="w-full border border-slate-600 hover:bg-slate-700 text-slate-200 font-semibold py-3 rounded-lg flex items-center justify-center gap-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
               >
                 <MessageCircle className="w-4 h-4 text-slate-400" />
                 تواصل معي عبر واتساب
               </button>
             </div>
             
             <div className="mt-4">
                <button onClick={() => router.push("/dashboard")} className="text-slate-500 hover:text-slate-300 text-sm hover:underline underline-offset-4">سأقوم بالدفع لاحقاً (العودة للوحة التحكم)</button>
             </div>
           </div>
        ) : null}
      </motion.div>
    </main>
  );
}
