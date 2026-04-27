-- ==========================================================
-- SQL Schema for Smart Lecture Summarizer (Supabase)
-- نظام اشتراكات + حماية + تحقق بالهاتف
-- ==========================================================

-- 1. جدول ملفات المستخدمين
CREATE TABLE public.profiles (
  id uuid REFERENCES auth.users ON DELETE CASCADE NOT NULL PRIMARY KEY,
  full_name text,
  phone_number text UNIQUE,
  phone_verified boolean DEFAULT false,
  is_blocked boolean DEFAULT false,
  role text DEFAULT 'user',           -- تم إضافة عمود الصلاحيات
  current_plan_id uuid,
  quota_remaining integer DEFAULT 0,
  quota_reset_at timestamp WITH TIME ZONE,
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_read_own_profile" ON public.profiles FOR SELECT USING (auth.uid() = id);

CREATE POLICY "users_update_own_profile_restricted" 
ON public.profiles FOR UPDATE 
USING (auth.uid() = id)
WITH CHECK (
  auth.uid() = id 
  AND role = (SELECT role FROM public.profiles WHERE id = auth.uid())
  AND quota_remaining = (SELECT quota_remaining FROM public.profiles WHERE id = auth.uid())
  AND is_blocked = (SELECT is_blocked FROM public.profiles WHERE id = auth.uid())
);

CREATE POLICY "users_insert_own_profile" ON public.profiles FOR INSERT 
WITH CHECK (
  auth.uid() = id 
  AND role = 'user'
  AND quota_remaining <= 1
);


-- 2. جدول الباقات
CREATE TABLE public.subscription_plans (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  name text NOT NULL,
  name_en text NOT NULL,
  period_type text NOT NULL CHECK (period_type IN ('weekly', 'monthly')),
  price_ils numeric(10,2) NOT NULL,
  price_usd numeric(10,2),
  lecture_quota integer NOT NULL,
  is_active boolean DEFAULT true,
  features jsonb DEFAULT '[]',
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.subscription_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anyone_can_read_plans" ON public.subscription_plans FOR SELECT USING (true);


-- 3. جدول اشتراكات المستخدمين
CREATE TABLE public.user_subscriptions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  plan_id uuid REFERENCES public.subscription_plans(id) NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'cancelled')),
  starts_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
  expires_at timestamp WITH TIME ZONE NOT NULL,
  lectures_used integer DEFAULT 0,
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.user_subscriptions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_subscriptions" ON public.user_subscriptions FOR SELECT USING (auth.uid() = user_id);


-- 4. جدول سجل الاستخدام
CREATE TABLE public.usage_logs (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  subscription_id uuid REFERENCES public.user_subscriptions(id),
  action_type text NOT NULL DEFAULT 'lecture_process',
  audio_duration_seconds integer,
  api_cost_usd numeric(10,4),
  status text DEFAULT 'success' CHECK (status IN ('success', 'failed', 'rate_limited')),
  chat_count integer DEFAULT 0,
  metadata jsonb DEFAULT '{}',
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_logs" ON public.usage_logs FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "users_insert_own_logs" ON public.usage_logs FOR INSERT WITH CHECK (auth.uid() = user_id);


-- 5. جدول المعاملات المالية
CREATE TABLE public.transactions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  short_order_id text UNIQUE NOT NULL,
  user_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  plan_id uuid REFERENCES public.subscription_plans(id) NOT NULL,
  amount numeric(10,2) NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'rejected')),
  payment_method text DEFAULT 'reflect',
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_transactions" ON public.transactions FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "users_insert_own_transactions" ON public.transactions FOR INSERT 
WITH CHECK (auth.uid() = user_id AND status = 'pending');


-- 6. جدول المحاضرات
CREATE TABLE public.lectures (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  title text NOT NULL DEFAULT 'محاضرة جديدة',
  transcript text,
  summary text,
  glossary jsonb,
  emphasis jsonb,
  pdf_html text,
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.lectures ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_manage_own_lectures" ON public.lectures FOR ALL USING (auth.uid() = user_id);


-- 7. إدخال الباقات
INSERT INTO public.subscription_plans (name, name_en, period_type, price_ils, price_usd, lecture_quota, features) VALUES
('أسبوعي - طالب',    'Weekly Student',   'weekly',  6.00,   1.64,  5,   '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز"]'),
('أسبوعي - متقدم',   'Weekly Advanced',  'weekly',  15.00,  4.10,  15,  '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز", "PDF Web View"]'),
('أسبوعي - VIP',      'Weekly VIP',       'weekly',  25.00,  6.85,  30,  '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز", "PDF Web View", "أولوية في المعالجة"]'),
('شهري - أساسي',     'Monthly Basic',    'monthly', 25.00,  6.85,  20,  '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز"]'),
('شهري - متقدم',     'Monthly Advanced', 'monthly', 45.00,  12.30, 50,  '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز", "PDF Web View", "حفظ المحاضرات"]'),
('شهري - برو',       'Monthly Pro',      'monthly', 80.00,  21.90, 100, '["تلخيص ذكي", "قاموس سياقي", "نقاط تركيز", "PDF Web View", "حفظ المحاضرات", "أولوية في المعالجة"]');


-- 8. تفعيل Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE public.lectures;
ALTER PUBLICATION supabase_realtime ADD TABLE public.user_subscriptions;
