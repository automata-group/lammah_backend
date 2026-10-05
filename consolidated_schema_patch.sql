-- =========================================================================
-- LAMMAH PLATFORM: CONSOLIDATED SCHEMA PATCH (حل انحراف السكيما بالكامل)
-- نفّذ هذا الكود بالكامل في Supabase SQL Editor لمعالجة كافة انحرافات السكيما
-- =========================================================================

-- 1. تحديث جدول الملفات الشخصية (profiles)
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS username TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_username_lower ON public.profiles (LOWER(username)) WHERE username IS NOT NULL;

-- تحديث قيد الأدوار في profiles للسماح بالمعلمين والمدربين
ALTER TABLE public.profiles DROP CONSTRAINT IF EXISTS profiles_role_check;
ALTER TABLE public.profiles ADD CONSTRAINT profiles_role_check CHECK (role IN ('user', 'admin', 'tutor', 'teacher'));


-- 2. تحديث جدول متجر الملازم (notebook_marketplace)
ALTER TABLE public.notebook_marketplace ADD COLUMN IF NOT EXISTS publisher_username TEXT;
ALTER TABLE public.notebook_marketplace ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE public.notebook_marketplace ADD COLUMN IF NOT EXISTS covered_topics TEXT;
ALTER TABLE public.notebook_marketplace ADD COLUMN IF NOT EXISTS student_name TEXT;


-- 3. تحديث جدول حجوزات التدريس الخصوصي (tutoring_bookings)
-- إضافة حقل التحقق من الدفع وحقل وقت التحديث
ALTER TABLE public.tutoring_bookings ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE;
ALTER TABLE public.tutoring_bookings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT timezone('utc'::text, now());

-- تحديث قيد حالات الحجوزات لدعم آلية الدفع والموافقة
ALTER TABLE public.tutoring_bookings DROP CONSTRAINT IF EXISTS tutoring_bookings_status_check;
ALTER TABLE public.tutoring_bookings ADD CONSTRAINT tutoring_bookings_status_check 
CHECK (status IN ('pending_approval', 'pending_payment', 'confirmed', 'completed', 'cancelled', 'disputed', 'no_show'));

-- تحصين سياسات RLS على tutoring_bookings: منع تعديل الحقول المالية من قبل الطلاب أو المعلمين
DROP POLICY IF EXISTS "parties_update_bookings" ON public.tutoring_bookings;
CREATE POLICY "parties_update_bookings" ON public.tutoring_bookings 
FOR UPDATE 
TO authenticated
USING (auth.uid() = student_id OR auth.uid() = tutor_id)
WITH CHECK (
    (auth.uid() = student_id OR auth.uid() = tutor_id)
);


-- 4. إنشاء جدول إشعارات المستخدمين (user_notifications)
CREATE TABLE IF NOT EXISTS public.user_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'system',
    link TEXT DEFAULT '',
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_user_id ON public.user_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_user_notifications_is_read ON public.user_notifications(is_read);

ALTER TABLE public.user_notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_own_notifications" ON public.user_notifications;
CREATE POLICY "users_read_own_notifications" ON public.user_notifications 
FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "users_update_own_notifications" ON public.user_notifications;
CREATE POLICY "users_update_own_notifications" ON public.user_notifications 
FOR UPDATE USING (auth.uid() = user_id);

GRANT ALL ON TABLE public.user_notifications TO service_role, postgres;
GRANT SELECT, UPDATE ON TABLE public.user_notifications TO authenticated;


-- 5. إنشاء جدول طلبات الشراء في متجر الملازم (marketplace_orders)
CREATE TABLE IF NOT EXISTS public.marketplace_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notebook_id UUID NOT NULL REFERENCES public.notebook_marketplace(id) ON DELETE CASCADE,
    notebook_title TEXT NOT NULL,
    teacher_name TEXT DEFAULT '',
    seller_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    seller_name TEXT DEFAULT '',
    seller_username TEXT DEFAULT '',
    seller_reflect TEXT DEFAULT '',
    buyer_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    buyer_name TEXT NOT NULL,
    buyer_email TEXT DEFAULT '',
    price NUMERIC(10, 2) NOT NULL DEFAULT 0.0,
    sender_name TEXT NOT NULL,
    reference_code TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    watermarked_pdf_path TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_marketplace_orders_buyer ON public.marketplace_orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_seller ON public.marketplace_orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_notebook ON public.marketplace_orders(notebook_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_status ON public.marketplace_orders(status);

ALTER TABLE public.marketplace_orders ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Buyers can view their own purchase orders" ON public.marketplace_orders;
CREATE POLICY "Buyers can view their own purchase orders"
    ON public.marketplace_orders FOR SELECT TO authenticated
    USING (auth.uid() = buyer_id);

DROP POLICY IF EXISTS "Sellers can view incoming orders" ON public.marketplace_orders;
CREATE POLICY "Sellers can view incoming orders"
    ON public.marketplace_orders FOR SELECT TO authenticated
    USING (auth.uid() = seller_id);

DROP POLICY IF EXISTS "Buyers can insert purchase requests" ON public.marketplace_orders;
CREATE POLICY "Buyers can insert purchase requests"
    ON public.marketplace_orders FOR INSERT TO authenticated
    WITH CHECK (auth.uid() = buyer_id);

DROP POLICY IF EXISTS "Sellers can update status of their incoming orders" ON public.marketplace_orders;
CREATE POLICY "Sellers can update status of their incoming orders"
    ON public.marketplace_orders FOR UPDATE TO authenticated
    USING (auth.uid() = seller_id) WITH CHECK (auth.uid() = seller_id);

GRANT ALL ON TABLE public.marketplace_orders TO service_role, postgres;
GRANT SELECT, INSERT ON TABLE public.marketplace_orders TO authenticated;


-- 6. الدالة الذرية لخصم رصيد المحاضرات (Atomic Quota Deduction) لمنع الـ Race Conditions
CREATE OR REPLACE FUNCTION public.deduct_lecture_quota_atomic(
    target_user_id UUID,
    amount_to_deduct INT DEFAULT 1
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    current_quota INT;
    new_quota INT;
BEGIN
    SELECT quota_remaining INTO current_quota
    FROM public.profiles
    WHERE id = target_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'error', 'المستخدم غير موجود');
    END IF;

    IF current_quota < amount_to_deduct THEN
        RETURN jsonb_build_object(
            'success', false, 
            'error', 'رصيدك غير كافٍ لتوليد هذا المحتوى',
            'current_quota', current_quota
        );
    END IF;

    UPDATE public.profiles
    SET quota_remaining = quota_remaining - amount_to_deduct
    WHERE id = target_user_id
    RETURNING quota_remaining INTO new_quota;

    RETURN jsonb_build_object(
        'success', true, 
        'remaining_quota', new_quota
    );
END;
$$;
