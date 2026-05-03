package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/joho/godotenv"
)

const (
	MaxWorkers = 5
	ChunkSize  = 10 * 1024 * 1024
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

// 1. تحسين اتصال HTTP لتجنب استنزاف الموارد
var httpClient = &http.Client{
	Timeout: 5 * time.Second,
	Transport: &http.Transport{
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
	},
}

var jobQueue = make(chan ProcessingJob, 100)

// 2. استخدام sync.Pool لإعادة تدوير الذاكرة وتخفيف العبء على الـ Garbage Collector
var bufferPool = sync.Pool{
	New: func() interface{} {
		b := make([]byte, 1024*1024) // 1MB buffer
		return &b
	},
}

// القوائم البيضاء للحماية
var allowedExt = map[string]bool{
	".mp3": true, ".wav": true, ".m4a": true, ".pdf": true, ".ogg": true, ".flac": true,
}

var allowedMimeTypes = map[string]bool{
	"audio/mpeg": true, "audio/wav": true, "audio/x-wav": true,
	"audio/mp4": true, "audio/x-m4a": true, "application/pdf": true,
	"audio/ogg": true, "audio/flac": true, "audio/x-flac": true,
	"application/octet-stream": true, // السماح بالنوع العام للملفات الثنائية (شائع في m4a)
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

	// بدء تشغيل العمال (Workers)
	for i := 1; i <= MaxWorkers; i++ {
		go fileWorker(i)
	}

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
			}

			// التحقق إذا كان الأصل الحالي مسموحاً به
			isAllowed := false
			if origin == "" {
				isAllowed = true // للطلبات المباشرة (مثل curl)
			} else {
				for _, o := range allowedOrigins {
					if origin == o {
						isAllowed = true
						break
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

	log.Printf("🚀 Go Uploader starting with %d workers on port %s...", MaxWorkers, port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatal(err)
	}
}

// عامل المعالجة الرئيسي الذي يستقبل المهام من الطابور
func fileWorker(id int) {
	log.Printf("Worker %d: Ready", id)
	for job := range jobQueue {
		// يتم تمرير المهمة لدالة منفصلة لضمان عمل defer بشكل صحيح وتفريغ الذاكرة
		processJob(id, job)
	}
}

// دالة لمعالجة المهمة وتوجيهها وإدارة دورة حياتها
func processJob(workerID int, job ProcessingJob) {
	startTime := time.Now()

	// التأكد من حذف الملف الأصلي المؤقت بعد الانتهاء
	defer func() {
		err := os.Remove(job.FilePath)
		if err != nil && !os.IsNotExist(err) {
			log.Printf("Worker %d: Failed to delete temp file %s: %v", workerID, job.FilePath, err)
		} else {
			log.Printf("Worker %d: Successfully deleted temp file: %s", workerID, job.FileName)
		}
	}()

	info, err := os.Stat(job.FilePath)
	if err != nil {
		log.Printf("Worker %d: Error accessing file %s: %v", workerID, job.FileName, err)
		return
	}

	log.Printf("Worker %d: Started processing %s (Size: %d MB)", workerID, job.FileName, info.Size()/(1024*1024))

	ext := strings.ToLower(filepath.Ext(job.FileName))

	// توجيه الملف للدالة الحقيقية المناسبة بناءً على نوعه
	switch {
	case ext == ".pdf":
		err = processPDF(workerID, job.FilePath)
	case allowedExt[ext]: // باقي الملفات الصوتية المسموحة
		err = processAudio(workerID, job.FilePath, ext)
	default:
		log.Printf("Worker %d: Unsupported extension during processing: %s", workerID, ext)
		return
	}

	if err != nil {
		log.Printf("Worker %d: Error processing %s: %v", workerID, job.FileName, err)
		return
	}

	log.Printf("Worker %d: Finished %s in %v", workerID, job.FileName, time.Since(startTime))
}

// دالة معالجة ملفات PDF الحقيقية
func processPDF(workerID int, filePath string) error {
	log.Printf("Worker %d: Extracting text from PDF...", workerID)

	// إنشاء مسار للملف النصي الناتج
	txtOutputPath := strings.TrimSuffix(filePath, filepath.Ext(filePath)) + ".txt"

	// استخدام أداة pdftotext (يجب أن تكون مثبتة على السيرفر عبر poppler-utils)
	// الأمر: pdftotext input.pdf output.txt
	cmd := exec.Command("pdftotext", filePath, txtOutputPath)

	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	err := cmd.Run()
	if err != nil {
		return fmt.Errorf("pdftotext failed: %v, stderr: %s", err, stderr.String())
	}

	// يمكنك هنا قراءة الملف النصي الناتج وإرساله لقاعدة البيانات أو لنموذج الذكاء الاصطناعي
	log.Printf("Worker %d: Successfully extracted text to %s", workerID, txtOutputPath)

	// تنظيف الملف النصي بعد الانتهاء من استخدامه (اختياري حسب منطق التطبيق لديك)
	// defer os.Remove(txtOutputPath)

	return nil
}

// دالة معالجة الملفات الصوتية الحقيقية
func processAudio(workerID int, filePath string, extension string) error {
	log.Printf("Worker %d: Converting and compressing audio file...", workerID)

	// إنشاء مسار للملف الصوتي المعالج بصيغة mp3
	mp3OutputPath := strings.TrimSuffix(filePath, extension) + "_processed.mp3"

	// استخدام FFmpeg لتحويل أي صيغة إلى MP3 بـ bitrate 128k لتقليل الحجم قبل النسخ الصوتي (Speech-to-Text)
	// الأمر: ffmpeg -y -i input.wav -b:a 128k output.mp3
	cmd := exec.Command("ffmpeg", "-y", "-i", filePath, "-b:a", "128k", mp3OutputPath)

	var stderr bytes.Buffer
	cmd.Stderr = &stderr // FFmpeg يكتب مخرجاته عادة في الـ stderr

	err := cmd.Run()
	if err != nil {
		return fmt.Errorf("ffmpeg failed: %v, stderr: %s", err, stderr.String())
	}

	log.Printf("Worker %d: Successfully processed audio to %s", workerID, mp3OutputPath)

	// هنا يمكنك رفع الملف المعالج (mp3OutputPath) إلى S3 أو إرساله لخدمة خارجية
	// defer os.Remove(mp3OutputPath)

	return nil
}

func handleUpload(w http.ResponseWriter, r *http.Request) {
	log.Printf("📥 Received %s request for %s from %s", r.Method, r.URL.Path, r.RemoteAddr)
	
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	token := r.Header.Get("Authorization")
	if token == "" {
		log.Printf("❌ No Authorization header present")
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	if !verifyToken(token) {
		log.Printf("❌ Token verification failed")
		http.Error(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	log.Printf("✅ Token verified, reading body...")
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

			if !allowedMimeTypes[mimeType] {
				log.Printf("Security alert: Blocked upload with malicious MIME type: %s for extension: %s", mimeType, ext)
				http.Error(w, "Invalid file content", http.StatusUnsupportedMediaType)
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

			// 5. التعامل الآمن مع امتلاء طابور المعالجة
			select {
			case jobQueue <- ProcessingJob{FilePath: finalPath, FileName: savedFilename}:
				log.Printf("Job queued for %s", savedFilename)
			default:
				// السيرفر مضغوط والعمال مشغولون بالكامل والطابور ممتلئ
				os.Remove(finalPath) // احذف الملف لتجنب امتلاء القرص
				log.Printf("Error: Job queue full, rejected %s", savedFilename)
				http.Error(w, "Server is too busy, please try again later", http.StatusServiceUnavailable)
				return
			}
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
		Status:   "Processing",
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

	req, _ := http.NewRequest("GET", supabaseURL+"/auth/v1/user", nil)
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

	return resp.StatusCode == http.StatusOK
}
