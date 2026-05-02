"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Loader2, Check, AlertCircle, X, MessageCircle, Users, Zap, BarChart3, Activity, Package, ShieldAlert } from "lucide-react";

export default function AdminPage() {
  const { user, session, loading } = useAuth();
  const router = useRouter();
  
  const [transactions, setTransactions] = useState<any[]>([]);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState("");
  const [processing, setProcessing] = useState<string | null>(null);
  const [stats, setStats] = useState<any>(null);
  const [usageStats, setUsageStats] = useState<any>(null);

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
      return;
    }
    if (session) fetchTransactions();
  }, [user, loading, session]);
  
  const fetchTransactions = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/admin/pending`, {
         headers: { "Authorization": `Bearer ${session?.access_token}` }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail?.user_message || "فشل الجلب - ربما لست أدمن");
      setTransactions(data);

      try {
        const usageRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/admin/stats`, {
           headers: { "Authorization": `Bearer ${session?.access_token}` }
        });
        const usageData = await usageRes.json();
        if (usageRes.ok) {
          setUsageStats(usageData);
        } else {
          setError(`خطأ في جلب الإحصائيات: ${usageData.detail || 'غير معروف'}`);
        }
      } catch (err) {
        console.error("Failed to fetch usage stats:", err);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFetching(false);
    }
  };
  
  const handleApprove = async (txId: string) => {
    if (!confirm("هل أنت متأكد من تأكيد هذا الدفع وتفعيل الاشتراك؟")) return;
    setProcessing(txId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/admin/${txId}/approve`, {
         method: "POST",
         headers: { "Authorization": `Bearer ${session?.access_token}` }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail?.user_message || "فشل التأكيد");
      
      setTransactions(prev => prev.filter(t => t.id !== txId));
      alert("تمت الموافقة بنجاح وتفعيل الباقة!");
    } catch (e: any) {
      alert(`خطأ: ${e.message}`);
    } finally {
      setProcessing(null);
    }
  };

  const handleReject = async (txId: string) => {
    if (!confirm("هل أنت متأكد من رفض هذا الطلب؟ لا يمكن التراجع عن هذا الإجراء.")) return;
    setProcessing(txId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8500"}/api/v1/transactions/admin/${txId}/reject`, {
         method: "POST",
         headers: { "Authorization": `Bearer ${session?.access_token}` }
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail?.user_message || "فشل الرفض");
      
      setTransactions(prev => prev.filter(t => t.id !== txId));
      alert("تم رفض الطلب وإزالته بنجاح.");
    } catch (e: any) {
      alert(`خطأ: ${e.message}`);
    } finally {
      setProcessing(null);
    }
  };

  if (loading || fetching) return <div className="min-h-screen bg-slate-900 flex items-center justify-center"><Loader2 className="w-10 h-10 text-indigo-400 animate-spin" /></div>;

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 p-8 flex flex-col items-center justify-center text-center" dir="rtl">
        <AlertCircle className="w-16 h-16 text-red-500 mb-4" />
        <h1 className="text-2xl font-bold text-white mb-2">تسجيل دخول غير مصرح</h1>
        <p className="text-slate-400">{error}</p>
        <button onClick={() => router.push("/")} className="mt-8 text-indigo-400">العودة للرئيسية</button>
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 p-8 flex justify-center" dir="rtl">
      <div className="max-w-5xl w-full">
        <div className="flex justify-between items-center mb-8">
            <h1 className="text-3xl font-bold text-white">لوحة تحكم الإدارة</h1>
            <p className="text-slate-400">الطلبات المعلقة</p>
        </div>
        
        {transactions.length === 0 ? (
          <div className="bg-slate-800 rounded-2xl p-12 text-center text-slate-400 border border-slate-700">
            لا توجد طلبات مدفوعات معلقة حالياً.
          </div>
        ) : (
          <div className="overflow-x-auto bg-slate-800 rounded-2xl border border-slate-700 shadow-xl">
            <table className="w-full text-right border-collapse">
              <thead>
                <tr className="bg-slate-800/50 border-b border-slate-700 text-slate-300 text-sm">
                  <th className="p-4 font-semibold">رقم الطلب</th>
                  <th className="p-4 font-semibold">اسم الطالب</th>
                  <th className="p-4 font-semibold">رقم الهاتف</th>
                  <th className="p-4 font-semibold">الباقة</th>
                  <th className="p-4 font-semibold">المبلغ</th>
                  <th className="p-4 font-semibold">التاريخ</th>
                  <th className="p-4 font-semibold text-center w-48">إجراءات</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50 text-sm">
                {transactions.map(tx => (
                  <tr key={tx.id} className="hover:bg-slate-700/30 transition-colors">
                    <td className="p-4 font-mono text-indigo-300">{tx.short_order_id}</td>
                    <td className="p-4 font-medium text-white">{tx.profiles?.full_name || 'غير مجدول'}</td>
                    <td className="p-4 dir-ltr text-left text-slate-300">
                      <div className="flex items-center justify-end gap-3">
                        {tx.profiles?.phone_number && (
                          <a href={`https://wa.me/${tx.profiles.phone_number.replace(/\D/g, '')}`} target="_blank" rel="noopener noreferrer" className="text-green-500 hover:text-green-400 transition-colors bg-green-500/10 p-1.5 rounded-full" title="مراسلة الطالب عبر واتساب">
                            <MessageCircle className="w-4 h-4" />
                          </a>
                        )}
                        <span>{tx.profiles?.phone_number || '---'}</span>
                      </div>
                    </td>
                    <td className="p-4">{tx.subscription_plans?.name || '---'}</td>
                    <td className="p-4 font-bold text-green-400">{tx.amount} ₪</td>
                    <td className="p-4 text-slate-400">{new Date(tx.created_at).toLocaleString('ar-EG')}</td>
                    <td className="p-4 text-center">
                      <div className="flex items-center justify-center gap-2">
                        <button 
                          onClick={() => handleApprove(tx.id)}
                          disabled={processing === tx.id}
                          className="bg-green-600/90 hover:bg-green-500 text-white px-4 py-2 rounded-lg font-medium transition-colors flex items-center justify-center flex-1"
                          title="تأكيد الدفع"
                        >
                          {processing === tx.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Check className="w-4 h-4 ml-1" /> تأكيد</>}
                        </button>
                        
                        <button 
                          onClick={() => handleReject(tx.id)}
                          disabled={processing === tx.id}
                          className="bg-slate-700/80 hover:bg-red-500 border border-slate-600 hover:border-red-500 text-slate-300 hover:text-white px-3 py-2 rounded-lg transition-colors flex items-center justify-center outline-none"
                          title="رفض الدفع وإلغاء الطلب"
                        >
                          {processing === tx.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Stats Section */}
        {stats && (
          <div className="mt-12 space-y-8">
            <h2 className="text-2xl font-bold text-white mb-6">الحالة الصحية للنظام (API Keys)</h2>
            
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="bg-slate-800 rounded-2xl p-6 border border-slate-700 shadow-lg">
                <h3 className="text-slate-400 text-sm mb-2 font-medium">Groq API</h3>
                <p className="text-xl font-bold text-green-400">{stats.system_health?.groq}</p>
                <div className="mt-4 text-xs text-slate-500">مفتاح الاستنتاج الذكي الأساسي</div>
              </div>
              <div className="bg-slate-800 rounded-2xl p-6 border border-slate-700 shadow-lg">
                <h3 className="text-slate-400 text-sm mb-2 font-medium">Gemini API</h3>
                <p className="text-xl font-bold text-green-400">{stats.system_health?.gemini}</p>
                <div className="mt-4 text-xs text-slate-500">مفتاح الفهم العميق للشرائح والصور</div>
              </div>
              <div className="bg-red-900/20 rounded-2xl p-6 border border-red-500/30 shadow-lg relative overflow-hidden">
                <h3 className="text-red-300 text-sm mb-2 font-medium">الأخطاء (طلاب واجهوا مشاكل)</h3>
                <p className="text-3xl font-bold text-red-500">{stats.errors?.length || 0}</p>
                <div className="mt-4 text-xs text-red-400/70">أخر 20 محاولة فاشلة في استخدام المنصة</div>
                <AlertCircle className="w-16 h-16 absolute -bottom-4 -left-4 text-red-500/10 pointer-events-none" />
              </div>
            </div>

            {stats.errors?.length > 0 && (
              <div className="bg-slate-800 rounded-2xl border border-red-500/20 shadow-xl overflow-hidden mt-8">
                <div className="p-6 border-b border-slate-700 bg-slate-900/50">
                  <h3 className="text-lg font-bold text-red-400 flex items-center">
                    <AlertCircle className="w-5 h-5 ml-2" />
                    سجل أخطاء الطلاب المفصل
                  </h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-right border-collapse">
                    <thead>
                      <tr className="bg-slate-800/50 border-b border-slate-700 text-slate-300 text-sm">
                        <th className="p-4 font-semibold w-40">تاريخ الخطأ</th>
                        <th className="p-4 font-semibold w-48">اسم الطالب</th>
                        <th className="p-4 font-semibold w-32">نوع العملية</th>
                        <th className="p-4 font-semibold">تفاصيل ملف الخطأ (Error Log)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/50 text-sm">
                      {stats.errors.map((err: any) => (
                        <tr key={err.id} className="hover:bg-red-500/5 transition-colors">
                          <td className="p-4 text-slate-400 dir-ltr text-right text-xs">{new Date(err.created_at).toLocaleString('ar-EG')}</td>
                          <td className="p-4 font-medium text-white">
                            <div>{err.profiles?.full_name || 'غير معروف'}</div>
                            <div className="text-xs text-slate-500 mt-1">{err.profiles?.phone_number || ''}</div>
                          </td>
                          <td className="p-4"><span className="bg-slate-700 text-slate-300 px-2.5 py-1 rounded-full text-xs whitespace-nowrap">{err.action_type || 'غير محدد'}</span></td>
                          <td className="p-4">
                            <div className="text-red-300 max-w-sm truncate text-xs p-2 bg-slate-900/50 rounded-lg border border-red-500/10" title={err.metadata?.error || JSON.stringify(err.metadata)} dir="ltr text-left">
                              {err.metadata?.error || JSON.stringify(err.metadata)}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
        {/* New Usage & Security Monitoring Section */}
        {usageStats && (
          <div className="mt-16 space-y-8">
            <h2 className="text-2xl font-bold text-indigo-300 mb-6 flex items-center">
              <Check className="w-6 h-6 ml-2" />
              مراقبة الاستخدام والأمان
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="bg-slate-800/80 rounded-2xl p-6 border border-slate-700 shadow-xl">
                <h3 className="text-slate-400 text-sm mb-2 font-medium">إجمالي المسجلين</h3>
                <p className="text-3xl font-extrabold text-white">{usageStats.total_users}</p>
                <div className="mt-4 text-xs text-slate-500">منذ انطلاق المنصة</div>
              </div>
              <div className="bg-slate-800/80 rounded-2xl p-6 border border-slate-700 shadow-xl">
                <h3 className="text-slate-400 text-sm mb-2 font-medium">مستخدمي التجربة المجانية</h3>
                <p className="text-3xl font-extrabold text-amber-400">{usageStats.free_users_count}</p>
                <div className="mt-4 text-xs text-slate-500">حسابات لم تشترك بعد</div>
              </div>
              <div className="bg-indigo-900/20 rounded-2xl p-6 border border-indigo-500/30 shadow-xl">
                <h3 className="text-indigo-300 text-sm mb-2 font-medium">إجمالي المحاضرات المعالجة</h3>
                <p className="text-3xl font-extrabold text-indigo-400">{usageStats.total_lecture_usage}</p>
                <div className="mt-4 text-xs text-indigo-400/60">تفريغ + تلخيص + أسئلة</div>
              </div>
            </div>

            {/* Active Subscriptions Table */}
            <div className="bg-slate-800 rounded-2xl border border-slate-700 shadow-2xl overflow-hidden mt-10">
              <div className="p-6 border-b border-slate-700 bg-slate-900/50 flex justify-between items-center">
                <h3 className="text-lg font-bold text-white flex items-center">
                  <Package className="w-5 h-5 ml-2 text-indigo-400" />
                  متابعة استهلاك المشتركين النشطين
                </h3>
                <span className="text-xs text-slate-500 font-mono">حماية البث والمشاركة</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-right border-collapse">
                  <thead>
                    <tr className="bg-slate-800/50 border-b border-slate-700 text-slate-300 text-sm">
                      <th className="p-4 font-semibold">المشترك</th>
                      <th className="p-4 font-semibold">الباقة</th>
                      <th className="p-4 font-semibold">الاستهلاك</th>
                      <th className="p-4 font-semibold text-center">المتبقي</th>
                      <th className="p-4 font-semibold text-center">بصمة الجهاز (Fingerprint)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50 text-sm">
                    {usageStats.active_subscriptions?.map((sub: any, idx: number) => (
                      <tr key={idx} className="hover:bg-slate-700/30 transition-colors">
                        <td className="p-4">
                          <div className="font-bold text-white">{sub.user_name}</div>
                          <div className="text-xs text-slate-500 font-mono">{sub.phone}</div>
                        </td>
                        <td className="p-4">
                          <span className="bg-indigo-500/10 text-indigo-300 px-3 py-1 rounded-full text-xs font-bold border border-indigo-500/20">
                            {sub.plan}
                          </span>
                        </td>
                        <td className="p-4">
                           <div className="flex flex-col gap-1">
                              <div className="text-slate-300 font-medium">{sub.used} / {sub.quota}</div>
                              <div className="w-24 h-1.5 bg-slate-950 rounded-full overflow-hidden">
                                 <div className="bg-indigo-500 h-full" style={{ width: `${(sub.used / sub.quota) * 100}%` }}></div>
                              </div>
                           </div>
                        </td>
                        <td className="p-4 text-center font-bold text-green-400 text-lg">
                           {sub.remaining}
                        </td>
                        <td className="p-4 text-center">
                           <div className="text-[10px] font-mono text-slate-500 truncate w-32 mx-auto bg-slate-900 p-1 rounded border border-slate-700" title={sub.fingerprint}>
                              {sub.fingerprint || '---'}
                           </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
