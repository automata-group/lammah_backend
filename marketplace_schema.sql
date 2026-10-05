-- =========================================================================
-- LAMMAH PLATFORM: MARKETPLACE ORDERS SCHEMA & RLS SECURITY POLICIES
-- =========================================================================

-- 1. Create table for persistent marketplace orders
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

-- 2. Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_buyer ON public.marketplace_orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_seller ON public.marketplace_orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_notebook ON public.marketplace_orders(notebook_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_status ON public.marketplace_orders(status);

-- 3. Enable Row Level Security (RLS)
ALTER TABLE public.marketplace_orders ENABLE ROW LEVEL SECURITY;

-- 4. RLS Policies:
-- Buyers can view their own orders
CREATE POLICY "Buyers can view their own purchase orders"
    ON public.marketplace_orders
    FOR SELECT
    TO authenticated
    USING (auth.uid() = buyer_id);

-- Sellers can view orders placed for their notebooks
CREATE POLICY "Sellers can view incoming orders"
    ON public.marketplace_orders
    FOR SELECT
    TO authenticated
    USING (auth.uid() = seller_id);

-- Buyers can insert purchase requests for notebooks
CREATE POLICY "Buyers can insert purchase requests"
    ON public.marketplace_orders
    FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = buyer_id);

-- Sellers can update status (approve/reject) of orders directed to them
CREATE POLICY "Sellers can update status of their incoming orders"
    ON public.marketplace_orders
    FOR UPDATE
    TO authenticated
    USING (auth.uid() = seller_id)
    WITH CHECK (auth.uid() = seller_id);

-- Service role / Admin has full access
CREATE POLICY "Service role and admins have full access to orders"
    ON public.marketplace_orders
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
