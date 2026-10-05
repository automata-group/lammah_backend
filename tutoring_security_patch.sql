-- ====================================================================
-- تصحيح أمان جداول التدريس والمحفظة والتحصين المالي في Supabase
-- Security Patch for Tutoring, Wallet Escrow & Atomic Quota
-- ====================================================================

-- 1. إضافة عمود حالة الدفع للحجوزات لمنع توليد أرصدة وهمية
ALTER TABLE public.tutoring_bookings 
ADD COLUMN IF NOT EXISTS is_paid boolean DEFAULT false;

-- تحديث الحجوزات السابقة المكتملة لتكون مدفوعة
UPDATE public.tutoring_bookings 
SET is_paid = true 
WHERE status IN ('confirmed', 'completed');

-- 2. تأمين صلاحيات جدول الحجوزات (tutoring_bookings)
-- منع المستخدمين والطلاب من تعديل الحالات والأسعار والأرباح مباشرة من المتصفح
REVOKE UPDATE, DELETE ON TABLE public.tutoring_bookings FROM authenticated;

-- السماح بتعديل الملاحظات ورقم الهاتف فقط للمستخدم
GRANT UPDATE (student_phone, notes_from_student) ON TABLE public.tutoring_bookings TO authenticated;

-- سحب السياسة القديمة الفضفاضة
DROP POLICY IF EXISTS "parties_update_bookings" ON public.tutoring_bookings;

-- سياسة آمنة للتحديث المحدود
CREATE POLICY "parties_update_bookings_safe" ON public.tutoring_bookings 
FOR UPDATE USING (auth.uid() = student_id OR auth.uid() = tutor_id)
WITH CHECK (auth.uid() = student_id OR auth.uid() = tutor_id);

-- 3. تأمين جدول المحفظة (tutor_wallets)
-- سحب أي صلاحية تعديل مباشر من المستخدمين العاديين، الإدارة والباك إيند فقط عبر service_role
REVOKE INSERT, UPDATE, DELETE ON TABLE public.tutor_wallets FROM authenticated;
GRANT SELECT ON TABLE public.tutor_wallets TO authenticated;

DROP POLICY IF EXISTS "tutors_read_own_wallet" ON public.tutor_wallets;
CREATE POLICY "tutors_read_own_wallet" ON public.tutor_wallets 
FOR SELECT USING (auth.uid() = tutor_id);

-- 4. تأمين جدول سحب الأرباح (tutor_payouts)
REVOKE UPDATE, DELETE ON TABLE public.tutor_payouts FROM authenticated;
GRANT SELECT, INSERT ON TABLE public.tutor_payouts TO authenticated;

-- 5. دالة الخصم الذري لرصيد المحاضرات (Atomic Quota Deduction) لمنع ثغرات السباق والتنافس (Race Conditions)
CREATE OR REPLACE FUNCTION public.deduct_lecture_quota_atomic(
  p_user_id uuid,
  p_cost integer
)
RETURNS boolean AS $$
DECLARE
  v_current_quota integer;
BEGIN
  -- قفل السطر للتحديث الذري ومنع التزامن
  SELECT quota_remaining INTO v_current_quota
  FROM public.profiles
  WHERE id = p_user_id
  FOR UPDATE;

  IF v_current_quota IS NULL OR v_current_quota < p_cost THEN
    RETURN false;
  END IF;

  UPDATE public.profiles
  SET quota_remaining = v_current_quota - p_cost
  WHERE id = p_user_id;

  RETURN true;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
