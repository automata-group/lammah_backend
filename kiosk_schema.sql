-- =========================================================================
-- LAMMAH PLATFORM: CAMPUS PROJECTS KIOSK (كشك المشاريع) SCHEMA & RLS
-- =========================================================================

-- 1. جدول مشاريع الطلبة في الكشك
CREATE TABLE IF NOT EXISTS public.kiosk_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    owner_name TEXT NOT NULL,
    owner_username TEXT DEFAULT '',
    owner_faculty TEXT DEFAULT '', -- التخصص أو الكلية (مثل: هندسة، صيدلة، تمريض)
    title TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('food', 'sweets', 'crafts', 'services', 'printing', 'fashion', 'tech', 'other')),
    description TEXT NOT NULL,
    price_range TEXT DEFAULT '', -- مثل: 5 - 25 شيكل
    whatsapp_number TEXT NOT NULL, -- رقم الواتساب المعتمد لتلقي الطلبات
    campus_locations TEXT[] DEFAULT ARRAY['الحرم الجامعي']::TEXT[], -- نقاط التسليم المفضلة بالجامعة
    images TEXT[] DEFAULT ARRAY[]::TEXT[], -- روابط حتى 5 صور للمنتجات
    clicks_count INTEGER NOT NULL DEFAULT 0, -- عداد زيارات ونقرات المتجر
    likes_count INTEGER NOT NULL DEFAULT 0,
    rating_avg NUMERIC(3,2) NOT NULL DEFAULT 0.0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'archived')),
    rejection_reason TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 2. جدول الإعجابات للمشاريع (Like per user)
CREATE TABLE IF NOT EXISTS public.kiosk_project_likes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.kiosk_projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_kiosk_project_user_like UNIQUE (project_id, user_id)
);

-- 3. جدول المراجعات والتقييمات والتعليقات بالنجوم
CREATE TABLE IF NOT EXISTS public.kiosk_project_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.kiosk_projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    user_name TEXT NOT NULL,
    user_faculty TEXT DEFAULT '',
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    CONSTRAINT uq_kiosk_project_user_review UNIQUE (project_id, user_id)
);

-- 4. فهارس سريعة للأداء والاستعلام
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_status ON public.kiosk_projects(status);
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_category ON public.kiosk_projects(category);
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_owner ON public.kiosk_projects(owner_id);
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_clicks ON public.kiosk_projects(clicks_count DESC);
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_rating ON public.kiosk_projects(rating_avg DESC);
CREATE INDEX IF NOT EXISTS idx_kiosk_projects_likes ON public.kiosk_projects(likes_count DESC);
CREATE INDEX IF NOT EXISTS idx_kiosk_reviews_project ON public.kiosk_project_reviews(project_id);
CREATE INDEX IF NOT EXISTS idx_kiosk_likes_project ON public.kiosk_project_likes(project_id);

-- 5. دالة ذرية لزيادة عداد زيارات/ضغطات المتجر (Atomic Click Increment)
CREATE OR REPLACE FUNCTION public.increment_kiosk_project_clicks(p_project_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_new_clicks INTEGER;
BEGIN
    UPDATE public.kiosk_projects
    SET clicks_count = clicks_count + 1,
        updated_at = timezone('utc'::text, now())
    WHERE id = p_project_id
    RETURNING clicks_count INTO v_new_clicks;

    RETURN COALESCE(v_new_clicks, 0);
END;
$$;

-- 6. تريغر ذري لإعادة احتساب التقييمات والإعجابات والتعليقات تلقائياً
CREATE OR REPLACE FUNCTION public.sync_kiosk_project_stats()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_proj_id UUID;
BEGIN
    IF TG_TABLE_NAME = 'kiosk_project_reviews' THEN
        v_proj_id := COALESCE(NEW.project_id, OLD.project_id);
        UPDATE public.kiosk_projects
        SET rating_avg = COALESCE((SELECT ROUND(AVG(rating)::numeric, 2) FROM public.kiosk_project_reviews WHERE project_id = v_proj_id), 0.0),
            rating_count = COALESCE((SELECT COUNT(*) FROM public.kiosk_project_reviews WHERE project_id = v_proj_id), 0),
            comments_count = COALESCE((SELECT COUNT(*) FROM public.kiosk_project_reviews WHERE project_id = v_proj_id), 0),
            updated_at = timezone('utc'::text, now())
        WHERE id = v_proj_id;
    ELSIF TG_TABLE_NAME = 'kiosk_project_likes' THEN
        v_proj_id := COALESCE(NEW.project_id, OLD.project_id);
        UPDATE public.kiosk_projects
        SET likes_count = COALESCE((SELECT COUNT(*) FROM public.kiosk_project_likes WHERE project_id = v_proj_id), 0),
            updated_at = timezone('utc'::text, now())
        WHERE id = v_proj_id;
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_sync_kiosk_reviews ON public.kiosk_project_reviews;
CREATE TRIGGER trg_sync_kiosk_reviews
AFTER INSERT OR UPDATE OR DELETE ON public.kiosk_project_reviews
FOR EACH ROW EXECUTE FUNCTION public.sync_kiosk_project_stats();

DROP TRIGGER IF EXISTS trg_sync_kiosk_likes ON public.kiosk_project_likes;
CREATE TRIGGER trg_sync_kiosk_likes
AFTER INSERT OR DELETE ON public.kiosk_project_likes
FOR EACH ROW EXECUTE FUNCTION public.sync_kiosk_project_stats();

-- 7. تفعيل سياسات الحماية RLS (Row Level Security)
ALTER TABLE public.kiosk_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kiosk_project_likes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kiosk_project_reviews ENABLE ROW LEVEL SECURITY;

-- الجميع (بما فيهم الزوار) يمكنهم رؤية المشاريع المعتمدة فقط
CREATE POLICY "Anyone can view approved projects"
    ON public.kiosk_projects FOR SELECT
    USING (status = 'approved' OR auth.uid() = owner_id);

-- المستخدمون المسجلون يمكنهم تقديم مشاريع جديدة
CREATE POLICY "Users can create their own kiosk project"
    ON public.kiosk_projects FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = owner_id);

-- أصحاب المشاريع يمكنهم تعديل مشاريعهم
CREATE POLICY "Owners can update their own kiosk project"
    ON public.kiosk_projects FOR UPDATE
    TO authenticated
    USING (auth.uid() = owner_id)
    WITH CHECK (auth.uid() = owner_id);

-- سياسات الإعجابات
CREATE POLICY "Anyone can read kiosk likes"
    ON public.kiosk_project_likes FOR SELECT
    USING (true);

CREATE POLICY "Authenticated users can toggle likes"
    ON public.kiosk_project_likes FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can remove their own likes"
    ON public.kiosk_project_likes FOR DELETE
    TO authenticated
    USING (auth.uid() = user_id);

-- سياسات التقييمات والتعليقات
CREATE POLICY "Anyone can read kiosk reviews"
    ON public.kiosk_project_reviews FOR SELECT
    USING (true);

CREATE POLICY "Authenticated users can write review"
    ON public.kiosk_project_reviews FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their review"
    ON public.kiosk_project_reviews FOR UPDATE
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their review"
    ON public.kiosk_project_reviews FOR DELETE
    TO authenticated
    USING (auth.uid() = user_id);

-- صلاحيات كاملة لمفتاح الخدمة والمسؤولين
CREATE POLICY "Service role and admins full access to kiosk_projects"
    ON public.kiosk_projects FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

CREATE POLICY "Service role and admins full access to kiosk_likes"
    ON public.kiosk_project_likes FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

CREATE POLICY "Service role and admins full access to kiosk_reviews"
    ON public.kiosk_project_reviews FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

-- =========================================================================
-- 8. منح صلاحيات الجداول والدوال (ضروري لتجنب خطأ Permission Denied 42501)
-- =========================================================================
GRANT ALL ON TABLE public.kiosk_projects TO postgres, service_role;
GRANT ALL ON TABLE public.kiosk_project_likes TO postgres, service_role;
GRANT ALL ON TABLE public.kiosk_project_reviews TO postgres, service_role;

GRANT SELECT ON TABLE public.kiosk_projects TO anon, authenticated;
GRANT INSERT, UPDATE ON TABLE public.kiosk_projects TO authenticated;

GRANT SELECT ON TABLE public.kiosk_project_likes TO anon, authenticated;
GRANT INSERT, DELETE ON TABLE public.kiosk_project_likes TO authenticated;

GRANT SELECT ON TABLE public.kiosk_project_reviews TO anon, authenticated;
GRANT INSERT, UPDATE, DELETE ON TABLE public.kiosk_project_reviews TO authenticated;

GRANT EXECUTE ON FUNCTION public.increment_kiosk_project_clicks(UUID) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.sync_kiosk_project_stats() TO anon, authenticated, service_role;
