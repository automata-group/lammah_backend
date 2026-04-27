-- ==========================================================
-- Security Fixes for Lammah Platform
-- Execute this in Supabase SQL Editor
-- ==========================================================

-- 1. Add role to profiles and secure it
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS role text DEFAULT 'user';

-- 2. Add chat_count to usage_logs if missing
ALTER TABLE public.usage_logs ADD COLUMN IF NOT EXISTS chat_count integer DEFAULT 0;

-- 3. Create transactions table if missing
CREATE TABLE IF NOT EXISTS public.transactions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  short_order_id text UNIQUE NOT NULL,
  user_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  plan_id uuid REFERENCES public.subscription_plans(id) NOT NULL,
  amount numeric(10,2) NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'rejected')),
  payment_method text DEFAULT 'reflect',
  created_at timestamp WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 4. Enable RLS on all tables
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscription_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lectures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;

-- 5. Secure Profiles (Prevent self-upgrading quota/role)
DROP POLICY IF EXISTS "users_update_own_profile" ON public.profiles;
DROP POLICY IF EXISTS "users_update_own_profile_restricted" ON public.profiles;
CREATE POLICY "users_update_own_profile_restricted" 
ON public.profiles FOR UPDATE 
USING (auth.uid() = id)
WITH CHECK (
  auth.uid() = id 
  AND role = (SELECT role FROM public.profiles WHERE id = auth.uid())
  AND quota_remaining = (SELECT quota_remaining FROM public.profiles WHERE id = auth.uid())
  AND is_blocked = (SELECT is_blocked FROM public.profiles WHERE id = auth.uid())
);

DROP POLICY IF EXISTS "users_insert_own_profile" ON public.profiles;
CREATE POLICY "users_insert_own_profile_safe"
ON public.profiles FOR INSERT
WITH CHECK (
  auth.uid() = id 
  AND role = 'user' -- Force user role on creation
  AND quota_remaining <= 1 -- Limit initial quota
);

-- 6. Secure Subscription Plans (Anyone can read, only admin can write)
DROP POLICY IF EXISTS "anyone_can_read_plans" ON public.subscription_plans;
CREATE POLICY "anyone_can_read_plans" ON public.subscription_plans
FOR SELECT USING (true);

-- 7. Secure Transactions
DROP POLICY IF EXISTS "users_read_own_transactions" ON public.transactions;
CREATE POLICY "users_read_own_transactions" ON public.transactions
FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "users_insert_own_transactions" ON public.transactions;
CREATE POLICY "users_insert_own_transactions_safe" ON public.transactions
FOR INSERT WITH CHECK (
  auth.uid() = user_id 
  AND status = 'pending' -- Force pending status
);

-- 8. Fix Permissions (Revoke excessive grants)
REVOKE ALL ON public.subscription_plans FROM authenticated, anon;
GRANT SELECT ON public.subscription_plans TO authenticated, anon;

REVOKE ALL ON public.profiles FROM authenticated;
GRANT SELECT ON public.profiles TO authenticated;
GRANT UPDATE (full_name, phone_number) ON public.profiles TO authenticated;
GRANT INSERT ON public.profiles TO authenticated; -- For auto-creation

-- 9. Add Admin Policies (Only for users with role='admin')
CREATE POLICY "admins_manage_all_transactions" ON public.transactions
FOR ALL USING (
  EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND role = 'admin')
);

CREATE POLICY "admins_manage_all_profiles" ON public.profiles
FOR ALL USING (
  EXISTS (SELECT 1 FROM public.profiles WHERE id = auth.uid() AND role = 'admin')
);

-- 10. Audit: Verify that service_role is the only one with full access
-- (Supabase handles service_role automatically, but we ensure authenticated is restricted)
