"use client";

import { motion } from "framer-motion";
import { FileText, Sparkles, Download, Copy, BookOpen, Target, BrainCircuit, MessageCircle, Clock, CheckCircle } from "lucide-react";

export default function ResultSkeleton() {
  return (
    <div className="w-full max-w-6xl grid grid-cols-1 lg:grid-cols-3 gap-8 pb-20 animate-pulse">
      
      {/* Back Button Skeleton */}
      <div className="lg:col-span-3 flex justify-start mb-2">
        <div className="h-12 w-48 bg-slate-800/50 rounded-2xl border border-slate-700"></div>
      </div>

      <div className="lg:col-span-2 space-y-8">
        {/* PDF Card Skeleton */}
        <div className="bg-slate-800/60 border border-indigo-500/20 p-6 rounded-[2rem] shadow-2xl relative">
          <div className="flex justify-between items-center mb-6">
            <div className="h-8 w-48 bg-slate-700 rounded-lg"></div>
            <div className="h-10 w-40 bg-slate-700 rounded-xl"></div>
          </div>
          <div className="w-full h-[600px] rounded-2xl bg-slate-900/50 border border-slate-700 flex items-center justify-center">
            <div className="flex flex-col items-center gap-4">
              <div className="w-16 h-16 bg-slate-800 rounded-full animate-spin border-4 border-indigo-500/20 border-t-indigo-500"></div>
              <p className="text-slate-500 font-medium">جاري تلوين الشرائح بالذكاء الاصطناعي...</p>
            </div>
          </div>
        </div>
        
        {/* Transcript Card Skeleton */}
        <div className="bg-slate-800/40 border border-slate-700/50 rounded-[2.5rem] shadow-2xl overflow-hidden relative backdrop-blur-xl">
          <div className="p-8 pb-4">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
              <div className="flex items-center gap-5">
                <div className="w-14 h-14 bg-slate-700/50 rounded-2xl border border-slate-700"></div>
                <div className="space-y-2">
                  <div className="h-7 w-48 bg-slate-700 rounded-lg"></div>
                  <div className="h-4 w-64 bg-slate-700/50 rounded-lg"></div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="h-12 w-32 bg-slate-700/50 rounded-xl"></div>
                <div className="h-12 w-32 bg-slate-700/50 rounded-xl"></div>
              </div>
            </div>
          </div>

          <div className="px-8 pb-8">
            <div className="bg-slate-900/60 p-8 md:p-10 rounded-[2rem] border border-slate-700/50 shadow-inner relative overflow-hidden max-h-[700px]">
              <div className="space-y-6">
                <div className="h-4 w-32 bg-slate-800 rounded mb-8"></div>
                {[1, 2, 3, 4, 5, 6].map((i) => (
                  <div key={i} className="space-y-2">
                    <div className="h-4 w-full bg-slate-800/60 rounded"></div>
                    <div className="h-4 w-[90%] bg-slate-800/40 rounded"></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-6">
        {/* Tabs Header Skeleton */}
        <div className="flex flex-wrap gap-2 space-x-reverse bg-slate-800/80 p-1.5 rounded-2xl border border-slate-700">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="flex-1 min-w-[80px] h-12 bg-slate-700/30 rounded-xl"></div>
          ))}
        </div>

        {/* Tab Content Skeleton (Notes Style) */}
        <div className="space-y-6">
          <div className="bg-gradient-to-b from-indigo-900/10 to-slate-800/20 border border-indigo-500/10 p-6 rounded-[2rem] shadow-xl">
            <div className="h-6 w-48 bg-indigo-500/10 rounded-lg mb-6"></div>
            <div className="space-y-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="bg-slate-900/40 p-5 rounded-2xl border border-slate-700/30 space-y-3">
                  <div className="h-4 w-20 bg-indigo-500/10 rounded-full"></div>
                  <div className="h-3 w-full bg-slate-700/30 rounded"></div>
                  <div className="h-3 w-2/3 bg-slate-700/20 rounded"></div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-gradient-to-br from-purple-900/10 to-slate-800/20 border border-purple-500/10 p-6 rounded-[2rem] shadow-xl">
            <div className="h-6 w-48 bg-purple-500/10 rounded-lg mb-6"></div>
            <div className="space-y-4">
              {[1, 2].map((i) => (
                <div key={i} className="bg-slate-900/40 p-5 rounded-2xl border border-purple-500/20 space-y-3">
                  <div className="h-4 w-20 bg-purple-500/10 rounded-full"></div>
                  <div className="h-3 w-full bg-slate-700/30 rounded"></div>
                  <div className="h-3 w-5/6 bg-slate-700/20 rounded"></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
