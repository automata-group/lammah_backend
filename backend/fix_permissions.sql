-- ============================================
-- إصلاح صلاحيات الجداول - نفذ هذا في Supabase SQL Editor
-- ============================================

-- صلاحيات service_role (الباك إند)
GRANT ALL ON public.profiles TO service_role;
GRANT ALL ON public.subscription_plans TO service_role;
GRANT ALL ON public.user_subscriptions TO service_role;
GRANT ALL ON public.usage_logs TO service_role;

-- صلاحيات authenticated (المستخدم المسجل)
GRANT SELECT, INSERT, UPDATE ON public.profiles TO authenticated;
GRANT SELECT ON public.subscription_plans TO authenticated;
GRANT SELECT, INSERT ON public.user_subscriptions TO authenticated;
GRANT SELECT, INSERT ON public.usage_logs TO authenticated;

-- صلاحيات anon (الزائر بدون تسجيل)
GRANT SELECT ON public.subscription_plans TO anon;

-- تأكد أن postgres يملك الصلاحيات الكاملة
GRANT ALL ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO postgres;
