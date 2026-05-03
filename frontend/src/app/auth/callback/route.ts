import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const next = searchParams.get('next') ?? '/dashboard'

  console.log("📥 Callback received:", { code: code ? "exists" : "missing", origin, next });

  if (code) {
    const cookieStore = await cookies()

    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() {
            return cookieStore.getAll()
          },
          setAll(cookiesToSet) {
            cookiesToSet.forEach(({ name, value, options }) => {
              cookieStore.set(name, value, options)
            })
          },
        },
      }
    )

    const { error } = await supabase.auth.exchangeCodeForSession(code)

    if (!error) {
      // نجح تبادل الكود - أعد التوجيه للـ Dashboard
      return NextResponse.redirect(`${origin}${next}`)
    }

    console.error('Auth callback error:', error.message)
  }

  // في حالة الفشل - أعد للـ Login مع رسالة خطأ
  return NextResponse.redirect(`${origin}/login?error=auth_callback_failed`)
}
