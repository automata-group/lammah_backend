-- ====================================================================
-- SQL Schema for Lammah Peer Tutoring & 50/50 Revenue Split Platform
-- ====================================================================

-- 1. جدول ملفات المعلمين والمدربين الأكاديميين (Tutor Profiles)
CREATE TABLE IF NOT EXISTS public.tutor_profiles (
  id uuid REFERENCES public.profiles(id) ON DELETE CASCADE PRIMARY KEY,
  headline text NOT NULL,
  bio text,
  university text NOT NULL,
  major text NOT NULL,
  academic_level text NOT NULL DEFAULT 'student' CHECK (academic_level IN ('student', 'graduate', 'master', 'phd')),
  verification_doc_url text NOT NULL,
  intro_video_url text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
  rejection_reason text,
  rating_avg numeric(3,2) DEFAULT 5.00,
  rating_count integer DEFAULT 0,
  payout_details jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL,
  updated_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.tutor_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anyone_read_approved_tutors" ON public.tutor_profiles 
FOR SELECT USING (status = 'approved' OR auth.uid() = id);

CREATE POLICY "users_insert_own_tutor_profile" ON public.tutor_profiles 
FOR INSERT WITH CHECK (auth.uid() = id);

CREATE POLICY "tutors_update_own_profile" ON public.tutor_profiles 
FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);


-- 2. جدول الخدمات والكورسات المعروضة (Tutoring Services / Courses)
CREATE TABLE IF NOT EXISTS public.tutoring_services (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  tutor_id uuid REFERENCES public.tutor_profiles(id) ON DELETE CASCADE NOT NULL,
  title text NOT NULL,
  subject_name text NOT NULL,
  service_type text NOT NULL CHECK (service_type IN ('one_on_one', 'group_session', 'recorded_course')),
  price numeric(10,2) NOT NULL CHECK (price > 0),
  tutor_split_percent numeric(5,2) DEFAULT 50.00,
  duration_minutes integer DEFAULT 60,
  description text,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.tutoring_services ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anyone_read_active_services" ON public.tutoring_services 
FOR SELECT USING (is_active = true OR auth.uid() = tutor_id);

CREATE POLICY "tutors_manage_own_services" ON public.tutoring_services 
FOR ALL USING (auth.uid() = tutor_id);


-- 3. جدول الحجوزات والجلسات (Tutoring Bookings)
CREATE TABLE IF NOT EXISTS public.tutoring_bookings (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  service_id uuid REFERENCES public.tutoring_services(id) ON DELETE RESTRICT NOT NULL,
  student_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  tutor_id uuid REFERENCES public.tutor_profiles(id) ON DELETE CASCADE NOT NULL,
  session_date timestamptz NOT NULL,
  meeting_link text,
  status text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('pending_payment', 'confirmed', 'completed', 'cancelled', 'disputed')),
  total_price numeric(10,2) NOT NULL,
  tutor_share numeric(10,2) NOT NULL,    -- 50%
  platform_share numeric(10,2) NOT NULL, -- 50%
  is_settled boolean DEFAULT false,
  student_phone text,
  student_name text,
  notes_from_student text,
  created_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.tutoring_bookings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "parties_read_own_bookings" ON public.tutoring_bookings 
FOR SELECT USING (auth.uid() = student_id OR auth.uid() = tutor_id);

CREATE POLICY "students_create_bookings" ON public.tutoring_bookings 
FOR INSERT WITH CHECK (auth.uid() = student_id);

CREATE POLICY "parties_update_bookings" ON public.tutoring_bookings 
FOR UPDATE USING (auth.uid() = student_id OR auth.uid() = tutor_id);


-- 4. جدول محفظة المعلم (Tutor Wallets)
CREATE TABLE IF NOT EXISTS public.tutor_wallets (
  tutor_id uuid REFERENCES public.tutor_profiles(id) ON DELETE CASCADE PRIMARY KEY,
  available_balance numeric(10,2) DEFAULT 0.00 NOT NULL,
  pending_balance numeric(10,2) DEFAULT 0.00 NOT NULL,
  total_earned numeric(10,2) DEFAULT 0.00 NOT NULL,
  total_withdrawn numeric(10,2) DEFAULT 0.00 NOT NULL,
  updated_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.tutor_wallets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tutors_read_own_wallet" ON public.tutor_wallets 
FOR SELECT USING (auth.uid() = tutor_id);


-- 5. جدول طلبات سحب الأرباح (Tutor Payout Requests)
CREATE TABLE IF NOT EXISTS public.tutor_payouts (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  tutor_id uuid REFERENCES public.tutor_profiles(id) ON DELETE CASCADE NOT NULL,
  amount numeric(10,2) NOT NULL CHECK (amount > 0),
  payout_method text NOT NULL DEFAULT 'reflect',
  account_info jsonb NOT NULL,
  status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'processing', 'completed', 'rejected')),
  admin_notes text,
  receipt_image_url text,
  created_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL,
  settled_at timestamptz
);

ALTER TABLE public.tutor_payouts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tutors_read_own_payouts" ON public.tutor_payouts 
FOR SELECT USING (auth.uid() = tutor_id);

CREATE POLICY "tutors_create_payout_request" ON public.tutor_payouts 
FOR INSERT WITH CHECK (auth.uid() = tutor_id);


-- 6. جدول تقييمات الطلاب (Tutor Reviews)
CREATE TABLE IF NOT EXISTS public.tutor_reviews (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  booking_id uuid REFERENCES public.tutoring_bookings(id) ON DELETE CASCADE UNIQUE NOT NULL,
  tutor_id uuid REFERENCES public.tutor_profiles(id) ON DELETE CASCADE NOT NULL,
  student_id uuid REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  rating integer CHECK (rating >= 1 AND rating <= 5) NOT NULL,
  comment text,
  created_at timestamptz DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.tutor_reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "anyone_read_reviews" ON public.tutor_reviews 
FOR SELECT USING (true);

CREATE POLICY "students_create_reviews" ON public.tutor_reviews 
FOR INSERT WITH CHECK (auth.uid() = student_id);

-- 7. تفعيل الـ Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE public.tutoring_bookings;
ALTER PUBLICATION supabase_realtime ADD TABLE public.tutor_payouts;
