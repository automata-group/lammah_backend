package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/joho/godotenv"
)

const (
	ChunkSize = 10 * 1024 * 1024
)

type UploadResponse struct {
	TempID   string `json:"temp_id"`
	Filename string `json:"filename"`
	Status   string `json:"status"`
}

type ProcessingJob struct {
	FilePath string
	FileName string
}

type TokenCacheEntry struct {
	ExpiresAt time.Time
	Valid     bool
}

var (
	tokenCache   = make(map[string]TokenCacheEntry)
	tokenCacheMu sync.RWMutex
)

// 1. تحسين اتصال HTTP لتجنب استنزاف الموارد وضبط مهلة 15 ثانية للمرونة العالية
var httpClient = &http.Client{
	Timeout: 15 * time.Second,
	Transport: &http.Transport{
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
	},
}

// 2. استخدام sync.Pool لإعادة تدوير الذاكرة وتخفيف العبء على الـ Garbage Collector
var bufferPool = sync.Pool{
	New: func() interface{} {
		b := make([]byte, 1024*1024) // 1MB buffer
		return &b
	},
}

// القوائم البيضاء للحماية
var allowedExt = map[string]bool{
	".mp3": true, ".wav": true, ".m4a": true, ".pdf": true, ".ogg": true, ".opus": true,
	".aac": true, ".flac": true, ".pptx": true, ".ppt": true, ".webm": true, ".weba": true,
	".wma": true, ".mp4": true, ".m4v": true, ".mov": true, ".mkv": true,
}

var allowedMimeTypes = map[string]bool{
	"audio/mpeg": true, "audio/wav": true, "audio/x-wav": true,
	"audio/mp4": true, "audio/x-m4a": true, "application/pdf": true,
	"audio/ogg": true, "application/ogg": true, "audio/opus": true,
	"audio/flac": true, "audio/x-flac": true,
	"audio/aac": true, "audio/x-aac": true,
	"audio/webm": true, "video/webm": true,
	"video/mp4": true, "video/quicktime": true, "video/x-matroska": true,
	"audio/x-ms-wma": true, "audio/3gpp": true, "audio/amr": true,
	"application/octet-stream": true, // السماح بالنوع العام للملفات الثنائية (شائع في m4a/opus)
	"application/vnd.openxmlformats-officedocument.presentationml.presentation": true,
	"application/vnd.ms-powerpoint": true,
	"application/zip": true, // PowerPoint files are compressed zip archives under the hood
}

func main() {
	// تحميل الإعدادات بشكل هجين (Hybrid)
	envFiles := []string{".env.local", ".env", "../backend/.env", "../.env"}
	for _, f := range envFiles {
		if err := godotenv.Load(f); err == nil {
			log.Printf("✅ Loaded config from: %s", f)
		}
	}

	supabaseURL := os.Getenv("SUPABASE_URL")
	log.Printf("🛠️ Config: SUPABASE_URL=%s", supabaseURL)
	if supabaseURL == "" {
		log.Printf("⚠️ WARNING: SUPABASE_URL is empty! Auth will fail.")
	}
	port := os.Getenv("UPLOADER_PORT")
	if port == "" {
		port = "8080"
	}

	tempDir := os.Getenv("TEMP_DIR")
	if tempDir == "" {
		tempDir = "uploads/temp"
	}
	os.MkdirAll(tempDir, 0755)

	// بدء تشغيل معالج الوسائط المتقدم وطابور Redis
	go startRedisMediaWorker()

	// تعريف Middleware للتعامل مع CORS بشكل هجين
	corsMiddleware := func(next http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")

			// قائمة الأصول المسموح بها (الهجينة)
			allowedOrigins := []string{
				"https://lammah.site",
				"https://www.lammah.site",
				"https://api.lammah.site",
				"http://localhost:3000",
				"http://127.0.0.1:3000",
				"http://localhost:3001",
				"http://127.0.0.1:3001",
			}

			// التحقق إذا كان الأصل الحالي مسموحاً به
			isAllowed := false
			if origin == "" {
				isAllowed = true // للطلبات المباشرة (مثل curl)
			} else {
				if strings.HasSuffix(origin, "lammah.site") ||
					strings.HasSuffix(origin, "vercel.app") ||
					strings.Contains(origin, "localhost") ||
					strings.Contains(origin, "127.0.0.1") {
					isAllowed = true
				} else {
					for _, o := range allowedOrigins {
						if origin == o {
							isAllowed = true
							break
						}
					}
				}
			}

			if isAllowed {
				w.Header().Set("Access-Control-Allow-Origin", origin)
			} else {
				// في حالة عدم المطابقة، نسمح بطلب curl أو الطلبات التي لا تحتوي على origin
				w.Header().Set("Access-Control-Allow-Origin", "https://lammah.site")
			}

			w.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS, PUT, DELETE")
			w.Header().Set("Access-Control-Allow-Headers", "Accept, Content-Type, Content-Length, Accept-Encoding, X-CSRF-Token, Authorization")
			w.Header().Set("Access-Control-Allow-Credentials", "true")

			if r.Method == "OPTIONS" {
				w.WriteHeader(http.StatusOK)
				return
			}

			next(w, r)
		}
	}

	http.HandleFunc("/api/v1/summarize/upload-temp", corsMiddleware(handleUpload))
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})

	log.Printf("🚀 Go Uploader starting high-performance streaming server on port %s...", port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatal(err)
	}
}



func handleUpload(w http.ResponseWriter, r *http.Request) {
	log.Printf("📥 Received %s request for %s from %s", r.Method, r.URL.Path, r.RemoteAddr)

	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	token := r.Header.Get("Authorization")
	if token != "" {
		if verifyToken(token) {
			log.Printf("✅ Token verified, reading body...")
		} else {
			log.Printf("⚠️ Token verification warning - allowing permissive temp upload")
		}
	} else {
		log.Printf("ℹ️ Permissive temp upload without token header")
	}
	r.Body = http.MaxBytesReader(w, r.Body, 500*1024*1024)

	reader, err := r.MultipartReader()
	if err != nil {
		http.Error(w, "Invalid multipart request", http.StatusBadRequest)
		return
	}

	var savedFilename, originalFilename string
	tempDir := os.Getenv("TEMP_DIR")
	if tempDir == "" {
		tempDir = "uploads/temp"
	}

	for {
		part, err := reader.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			http.Error(w, "Error reading upload", http.StatusInternalServerError)
			return
		}

		if part.FormName() == "file" {
			originalFilename = part.FileName()
			// تنظيف الامتداد من أي أحرف خبيثة
			ext := strings.ToLower(strings.TrimSpace(filepath.Ext(originalFilename)))
			ext = strings.ReplaceAll(ext, "\x00", "")

			if !allowedExt[ext] {
				http.Error(w, "File extension not supported", http.StatusBadRequest)
				return
			}

			// 4. التحقق الفعلي من الـ MIME Type
			buffer := make([]byte, 512)
			n, err := part.Read(buffer)
			if err != nil && err != io.EOF {
				http.Error(w, "Error reading file signature", http.StatusInternalServerError)
				return
			}

			mimeType := http.DetectContentType(buffer[:n])
			// إزالة أي معلومات إضافية من نوع الـ MIME مثل charset
			mimeType = strings.Split(mimeType, ";")[0]

			isAllowedMime := allowedMimeTypes[mimeType] || 
				strings.HasPrefix(mimeType, "audio/") || 
				strings.HasPrefix(mimeType, "video/")

			if !isAllowedMime {
				log.Printf("Security alert: Blocked upload with malicious MIME type: %s for extension: %s", mimeType, ext)
				http.Error(w, "Invalid file content: "+mimeType, http.StatusUnsupportedMediaType)
				return
			}

			savedFilename = uuid.New().String() + ext
			finalPath := filepath.Join(tempDir, savedFilename)

			out, err := os.Create(finalPath)
			if err != nil {
				log.Printf("Error creating file: %v", err)
				http.Error(w, "Internal server error", http.StatusInternalServerError)
				return
			}

			multiReader := io.MultiReader(bytes.NewReader(buffer[:n]), part)

			// سحب Buffer من الـ Pool للنسخ السريع وبدون إرهاق الذاكرة
			copyBufPtr := bufferPool.Get().(*[]byte)
			_, err = io.CopyBuffer(out, multiReader, *copyBufPtr)

			// إرجاع الـ Buffer للـ Pool
			bufferPool.Put(copyBufPtr)
			out.Close()

			if err != nil {
				os.Remove(finalPath)
				log.Printf("Error saving file: %v", err)
				http.Error(w, "Error saving file", http.StatusInternalServerError)
				return
			}

			log.Printf("✅ File uploaded successfully: %s", savedFilename)
			break // قراءة أول ملف فقط لتجنب استغلال الثغرات برفع ملفات متعددة في نفس الطلب
		}
	}

	if savedFilename == "" {
		http.Error(w, "Missing file in request", http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(UploadResponse{
		TempID:   savedFilename,
		Filename: originalFilename,
		Status:   "Ready",
	})
}

func verifyToken(token string) bool {
	supabaseURL := os.Getenv("SUPABASE_URL")
	if supabaseURL == "" {
		return false
	}

	if !strings.HasPrefix(token, "Bearer ") {
		token = "Bearer " + token
	}

	// 1. فحص الكاش الداخلي (< 0.01ms) لتجنب الـ Rate-limit وخنق Supabase
	tokenCacheMu.RLock()
	entry, exists := tokenCache[token]
	tokenCacheMu.RUnlock()

	if exists && time.Now().Before(entry.ExpiresAt) {
		return entry.Valid
	}

	// 2. التحقق من Supabase بمهلة 15 ثانية
	req, err := http.NewRequest("GET", supabaseURL+"/auth/v1/user", nil)
	if err != nil {
		return false
	}
	req.Header.Set("Authorization", token)
	req.Header.Set("apikey", os.Getenv("SUPABASE_KEY"))

	resp, err := httpClient.Do(req)
	if err != nil {
		log.Printf("Auth check error: %v", err)
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("❌ Supabase Auth check failed with status: %d for URL: %s", resp.StatusCode, supabaseURL)
	}

	isValid := resp.StatusCode == http.StatusOK

	// 3. حفظ النتيجة في الكاش الداخلي لمدة 3 دقائق
	if isValid {
		tokenCacheMu.Lock()
		tokenCache[token] = TokenCacheEntry{
			ExpiresAt: time.Now().Add(3 * time.Minute),
			Valid:     true,
		}
		tokenCacheMu.Unlock()
	}

	return isValid
}
