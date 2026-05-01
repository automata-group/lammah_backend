import type { Metadata } from "next";
import { Tajawal } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/AuthContext";

const tajawal = Tajawal({ 
  subsets: ["arabic"], 
  weight: ["300", "400", "500", "700", "800", "900"],
  variable: '--font-tajawal'
});

export const metadata: Metadata = {
  title: "منصة لمّاح | Lammah",
  description: "تلخيص المحاضرات بالذكاء الاصطناعي مع استخراج القاموس السياقي ونقاط التركيز",
  openGraph: {
    title: "منصة لمّاح | Lammah",
    description: "تلخيص المحاضرات بالذكاء الاصطناعي مع استخراج القاموس السياقي ونقاط التركيز",
    images: [{ url: "/lammah-logo.png" }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <body className={`${tajawal.variable} font-sans antialiased`}>
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
