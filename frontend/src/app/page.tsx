"use client";
import { useAuth } from "@/context/AuthContext";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loader2, BookOpen, Sparkles } from "lucide-react";
import { motion } from "framer-motion";

export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading) {
      if (user) router.replace("/dashboard");
      else router.replace("/login");
    }
  }, [user, loading]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 flex items-center justify-center" dir="rtl">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center">
        <img src="/lammah-logo.png" alt="Lammah Logo" className="w-24 h-24 mx-auto mb-6 object-contain drop-shadow-2xl" />
        <h1 className="text-2xl font-bold text-slate-300 mb-4">منصة لمّاح</h1>
        <Loader2 className="w-8 h-8 text-indigo-400 animate-spin mx-auto" />
      </motion.div>
    </main>
  );
}
