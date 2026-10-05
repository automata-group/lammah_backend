-- Fix Free Trial Policy and Quotas
-- Run this in the Supabase SQL Editor or via a script

-- 1. Alter profiles table default value for quota_remaining
ALTER TABLE public.profiles ALTER COLUMN quota_remaining SET DEFAULT 15;

-- 2. Drop old insert policies and recreate a safer one allowing up to 15 minutes quota
DROP POLICY IF EXISTS "users_insert_own_profile" ON public.profiles;
DROP POLICY IF EXISTS "users_insert_own_profile_safe" ON public.profiles;

CREATE POLICY "users_insert_own_profile_safe" ON public.profiles
FOR INSERT
WITH CHECK (
  auth.uid() = id
  AND role = 'user'
  AND quota_remaining <= 15
);

-- 3. Reset existing users who have not used their free trial and have no active subscriptions
UPDATE public.profiles p
SET quota_remaining = 15
WHERE p.quota_remaining <= 1
  AND NOT EXISTS (
    SELECT 1 FROM public.user_subscriptions s
    WHERE s.user_id = p.id AND s.status = 'active'
  )
  AND NOT EXISTS (
    SELECT 1 FROM public.usage_logs l
    WHERE l.user_id = p.id AND l.action_type = 'lecture_process' AND l.status = 'success'
  );
