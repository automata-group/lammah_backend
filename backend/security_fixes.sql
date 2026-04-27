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
-- Function to check admin status without recursion
CREATE OR REPLACE FUNCTION public.is_admin() 
RETURNS boolean AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1 FROM public.profiles 
    WHERE id = auth.uid() AND role = 'admin'
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP POLICY IF EXISTS "users_read_own_profile" ON public.profiles;
CREATE POLICY "users_read_own_profile" ON public.profiles 
FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "users_update_own_profile_restricted" ON public.profiles;
CREATE POLICY "users_update_own_profile_restricted" 
ON public.profiles FOR UPDATE 
USING (auth.uid() = id)
WITH CHECK (auth.uid() = id); -- Restrictions handled by GRANT below

DROP POLICY IF EXISTS "users_insert_own_profile_safe" ON public.profiles;
CREATE POLICY "users_insert_own_profile_safe"
ON public.profiles FOR INSERT
WITH CHECK (
  auth.uid() = id 
  AND role = 'user' 
  AND quota_remaining <= 1
);

-- 6. Secure Subscription Plans
DROP POLICY IF EXISTS "anyone_can_read_plans" ON public.subscription_plans;
CREATE POLICY "anyone_can_read_plans" ON public.subscription_plans
FOR SELECT USING (true);

-- 7. Secure Transactions
DROP POLICY IF EXISTS "users_read_own_transactions" ON public.transactions;
CREATE POLICY "users_read_own_transactions" ON public.transactions
FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "users_insert_own_transactions_safe" ON public.transactions;
CREATE POLICY "users_insert_own_transactions_safe" ON public.transactions
FOR INSERT WITH CHECK (
  auth.uid() = user_id 
  AND status = 'pending'
);

-- 8. Fix Permissions (CRITICAL: This is what actually prevents field tampering)
REVOKE ALL ON public.profiles FROM authenticated;
GRANT SELECT ON public.profiles TO authenticated;
GRANT INSERT ON public.profiles TO authenticated;
-- Only allow updating specific fields
GRANT UPDATE (full_name, phone_number, phone_verified) ON public.profiles TO authenticated;

-- 9. Admin Policies (Using the non-recursive function)
DROP POLICY IF EXISTS "admins_manage_all_transactions" ON public.transactions;
CREATE POLICY "admins_manage_all_transactions" ON public.transactions
FOR ALL USING (is_admin());

DROP POLICY IF EXISTS "admins_manage_all_profiles" ON public.profiles;
CREATE POLICY "admins_manage_all_profiles" ON public.profiles
FOR ALL USING (is_admin());
