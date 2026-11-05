package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// MediaJob represents the work order dispatched from Python
type MediaJob struct {
	JobID        string   `json:"job_id"`
	UserID       string   `json:"user_id"`
	TaskType     string   `json:"task_type"` // "single_audio", "batch_audio", "pdf_extract"
	AudioPaths   []string `json:"audio_paths"`
	PDFPath      string   `json:"pdf_path,omitempty"`
	ChunkSeconds int      `json:"chunk_seconds"`
	OutputDir    string   `json:"output_dir"`
}

type ChunkResult struct {
	Index    int     `json:"index"`
	Path     string  `json:"path"`
	Duration float64 `json:"duration"`
	Filename string  `json:"filename"`
}

type MediaResult struct {
	JobID           string        `json:"job_id"`
	Status          string        `json:"status"` // "completed" | "failed"
	TotalDuration   float64       `json:"total_duration"`
	NumChunks       int           `json:"num_chunks"`
	MergedAudioPath string        `json:"merged_audio_path,omitempty"`
	Chunks          []ChunkResult `json:"chunks"`
	PDFTextPath     string        `json:"pdf_text_path,omitempty"`
	Error           string        `json:"error,omitempty"`
}

type MediaProgressEvent struct {
	JobID         string  `json:"job_id"`
	Status        string  `json:"status"`
	Percent       int     `json:"percent"`
	Step          string  `json:"step"`
	Message       string  `json:"message"`
	TotalDuration float64 `json:"total_duration,omitempty"`
}

var redisClient *redis.Client

func initRedis() *redis.Client {
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}

	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		log.Printf("⚠️ Invalid REDIS_URL '%s', falling back to localhost:6379: %v", redisURL, err)
		opt = &redis.Options{Addr: "localhost:6379"}
	}

	rdb := redis.NewClient(opt)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Printf("⚠️ Redis ping warning (%s): %v. Will retry in worker loop.", opt.Addr, err)
	} else {
		log.Printf("✅ Connected to Redis successfully at %s", opt.Addr)
	}

	return rdb
}

func publishProgress(ctx context.Context, rdb *redis.Client, jobID string, percent int, step, message string, totalDuration float64) {
	if rdb == nil {
		return
	}
	evt := MediaProgressEvent{
		JobID:         jobID,
		Status:        "processing",
		Percent:       percent,
		Step:          step,
		Message:       message,
		TotalDuration: totalDuration,
	}
	payload, _ := json.Marshal(evt)
	rdb.Publish(ctx, fmt.Sprintf("media:progress:%s", jobID), string(payload))
}

func probeDuration(filePath string) (float64, error) {
	cmd := exec.Command("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filePath)
	out, err := cmd.Output()
	if err != nil {
		return 0, fmt.Errorf("ffprobe failed on %s: %w", filePath, err)
	}
	durStr := strings.TrimSpace(string(out))
	dur, err := strconv.ParseFloat(durStr, 64)
	if err != nil {
		return 0, fmt.Errorf("failed to parse duration '%s': %w", durStr, err)
	}
	return dur, nil
}

// convertAudioToStandardizedMonoMP3 converts any audio/video to 64k mono MP3 (16kHz or 44.1kHz)
func convertAudioToStandardizedMonoMP3(srcPath, dstPath string) error {
	cmd := exec.Command("ffmpeg", "-y", "-i", srcPath, "-vn", "-acodec", "libmp3lame", "-b:a", "64k", "-ar", "44100", "-ac", "1", dstPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("ffmpeg convert failed: %v: %s", err, string(output))
	}
	return nil
}

// mergeMultipleAudios merges multiple audio files into a single unified timeline MP3
func mergeMultipleAudios(ctx context.Context, rdb *redis.Client, jobID string, audioPaths []string, dstPath, workDir string) (float64, error) {
	if len(audioPaths) == 0 {
		return 0, fmt.Errorf("no audio paths provided for merge")
	}

	if len(audioPaths) == 1 {
		publishProgress(ctx, rdb, jobID, 15, "converting", "🎧 جاري تحويل وضبط نقاء الصوت...", 0)
		if err := convertAudioToStandardizedMonoMP3(audioPaths[0], dstPath); err != nil {
			return 0, err
		}
		return probeDuration(dstPath)
	}

	// 1. Normalize each audio segment to standard intermediate MP3
	var intermediateFiles []string
	totalInputs := len(audioPaths)

	for i, p := range audioPaths {
		pct := 10 + int((float64(i)/float64(totalInputs))*25)
		publishProgress(ctx, rdb, jobID, pct, "normalizing", fmt.Sprintf("🎧 جاري تهيئة الفويس رقم %d من %d...", i+1, totalInputs), 0)

		intermPath := filepath.Join(workDir, fmt.Sprintf("interm_%d.mp3", i))
		if err := convertAudioToStandardizedMonoMP3(p, intermPath); err != nil {
			return 0, fmt.Errorf("failed to normalize audio %s: %w", p, err)
		}
		intermediateFiles = append(intermediateFiles, intermPath)
	}

	publishProgress(ctx, rdb, jobID, 38, "merging", "🔗 جاري دمج كافة الفويسات في شريط صوتي موحد فائق الدقة...", 0)

	// 2. Generate concat demuxer list
	concatListFile := filepath.Join(workDir, "concat_list.txt")
	var listContent strings.Builder
	for _, interm := range intermediateFiles {
		// Escape backslashes for FFmpeg demuxer
		safePath := filepath.ToSlash(interm)
		listContent.WriteString(fmt.Sprintf("file '%s'\n", safePath))
	}

	if err := os.WriteFile(concatListFile, []byte(listContent.String()), 0644); err != nil {
		return 0, fmt.Errorf("failed to write concat list: %w", err)
	}

	// 3. Fast stream-copy concat
	cmd := exec.Command("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concatListFile, "-c", "copy", dstPath)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return 0, fmt.Errorf("ffmpeg concat failed: %v: %s", err, string(output))
	}

	// Clean up intermediate files
	for _, interm := range intermediateFiles {
		_ = os.Remove(interm)
	}
	_ = os.Remove(concatListFile)

	return probeDuration(dstPath)
}

// chunkAudio splits master audio into 3-minute chunks (180s default) for Whisper
func chunkAudio(ctx context.Context, rdb *redis.Client, jobID, audioPath, outputDir string, chunkSeconds int) ([]ChunkResult, error) {
	if chunkSeconds <= 0 {
		chunkSeconds = 180
	}

	if err := os.MkdirAll(outputDir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create chunk dir: %w", err)
	}

	publishProgress(ctx, rdb, jobID, 45, "chunking", "✂️ جاري تقطيع المحاضرة لشنكات متوازية لمعالجة سريعة...", 0)

	chunkPattern := filepath.Join(outputDir, "chunk_%03d.mp3")
	cmd := exec.Command("ffmpeg", "-y", "-i", audioPath,
		"-f", "segment", "-segment_time", strconv.Itoa(chunkSeconds),
		"-c:a", "libmp3lame", "-b:a", "48k", "-ar", "16000", "-ac", "1",
		chunkPattern,
	)

	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("ffmpeg chunking failed: %v: %s", err, string(output))
	}

	// Read generated chunk files
	entries, err := os.ReadDir(outputDir)
	if err != nil {
		return nil, fmt.Errorf("failed to read chunk dir: %w", err)
	}

	var chunkFiles []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), "chunk_") && strings.HasSuffix(e.Name(), ".mp3") {
			chunkFiles = append(chunkFiles, e.Name())
		}
	}
	sort.Strings(chunkFiles)

	var results []ChunkResult
	for idx, fname := range chunkFiles {
		fullPath := filepath.Join(outputDir, fname)
		dur, _ := probeDuration(fullPath)
		results = append(results, ChunkResult{
			Index:    idx,
			Path:     fullPath,
			Duration: dur,
			Filename: fname,
		})
	}

	return results, nil
}

// processPDFExtract extracts text using pdftotext
func processPDFExtract(pdfPath, outTxtPath string) error {
	cmd := exec.Command("pdftotext", "-layout", pdfPath, outTxtPath)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("pdftotext failed: %v: %s", err, string(out))
	}
	return nil
}

// processMediaJob handles the end-to-end media job
func processMediaJob(ctx context.Context, rdb *redis.Client, job MediaJob) {
	log.Printf("🚀 [MEDIA_WORKER] Processing job %s for user %s (type=%s, files=%d)", job.JobID, job.UserID, job.TaskType, len(job.AudioPaths))

	workDir := filepath.Join(job.OutputDir, job.JobID)
	if err := os.MkdirAll(workDir, 0755); err != nil {
		log.Printf("❌ Failed to create workDir: %v", err)
		failMediaJob(ctx, rdb, job.JobID, fmt.Sprintf("Failed to create work directory: %v", err))
		return
	}

	publishProgress(ctx, rdb, job.JobID, 5, "starting", "🚀 بدأ محرك Go بمعالجة المقاطع...", 0)

	var masterAudioPath string
	var totalDuration float64
	var err error

	// 1. Audio Processing & Merging
	if len(job.AudioPaths) > 0 {
		masterAudioPath = filepath.Join(workDir, fmt.Sprintf("%s_master.mp3", job.JobID))
		totalDuration, err = mergeMultipleAudios(ctx, rdb, job.JobID, job.AudioPaths, masterAudioPath, workDir)
		if err != nil {
			log.Printf("❌ [MEDIA_WORKER] Audio merge failed for job %s: %v", job.JobID, err)
			failMediaJob(ctx, rdb, job.JobID, fmt.Sprintf("فشل دمج ومعالجة الصوت: %v", err))
			return
		}
		log.Printf("✅ [MEDIA_WORKER] Master audio created: %s (Duration: %.2fs)", masterAudioPath, totalDuration)
	}

	// 2. Audio Chunking
	var chunks []ChunkResult
	if masterAudioPath != "" {
		chunksDir := filepath.Join(workDir, "chunks")
		chunkSec := job.ChunkSeconds
		if chunkSec <= 0 {
			chunkSec = 180
		}
		chunks, err = chunkAudio(ctx, rdb, job.JobID, masterAudioPath, chunksDir, chunkSec)
		if err != nil {
			log.Printf("❌ [MEDIA_WORKER] Chunking failed for job %s: %v", job.JobID, err)
			failMediaJob(ctx, rdb, job.JobID, fmt.Sprintf("فشل تقطيع الصوت: %v", err))
			return
		}
		log.Printf("✅ [MEDIA_WORKER] Generated %d chunks for job %s", len(chunks), job.JobID)
	}

	// 3. PDF Extraction (if requested)
	var pdfTxtPath string
	if job.PDFPath != "" && fileExists(job.PDFPath) {
		publishProgress(ctx, rdb, job.JobID, 75, "pdf_extract", "📄 جاري استخراج نصوص السلايدات...", totalDuration)
		pdfTxtPath = filepath.Join(workDir, fmt.Sprintf("%s_slides.txt", job.JobID))
		if err := processPDFExtract(job.PDFPath, pdfTxtPath); err != nil {
			log.Printf("⚠️ PDF extraction non-fatal warning: %v", err)
			pdfTxtPath = ""
		}
	}

	// 4. Save Final Result in Redis with 2-hour TTL
	res := MediaResult{
		JobID:           job.JobID,
		Status:          "completed",
		TotalDuration:   totalDuration,
		NumChunks:       len(chunks),
		MergedAudioPath: masterAudioPath,
		Chunks:          chunks,
		PDFTextPath:     pdfTxtPath,
	}

	resBytes, _ := json.Marshal(res)
	resultKey := fmt.Sprintf("media:result:%s", job.JobID)
	if err := rdb.Set(ctx, resultKey, string(resBytes), 2*time.Hour).Err(); err != nil {
		log.Printf("❌ Failed to save result in Redis: %v", err)
	}

	// 5. Notify completion
	publishProgress(ctx, rdb, job.JobID, 100, "completed", "✨ تم تجهيز الصوت والشنكات بالكامل بنجاح!", totalDuration)
	log.Printf("🎉 [MEDIA_WORKER] Job %s completed successfully in Go engine!", job.JobID)
}

func failMediaJob(ctx context.Context, rdb *redis.Client, jobID, errorMsg string) {
	res := MediaResult{
		JobID:  jobID,
		Status: "failed",
		Error:  errorMsg,
	}
	resBytes, _ := json.Marshal(res)
	_ = rdb.Set(ctx, fmt.Sprintf("media:result:%s", jobID), string(resBytes), 2*time.Hour).Err()

	evt := MediaProgressEvent{
		JobID:   jobID,
		Status:  "failed",
		Percent: 100,
		Step:    "error",
		Message: errorMsg,
	}
	evtBytes, _ := json.Marshal(evt)
	rdb.Publish(ctx, fmt.Sprintf("media:progress:%s", jobID), string(evtBytes))
}

func fileExists(p string) bool {
	info, err := os.Stat(p)
	if err != nil {
		return false
	}
	return !info.IsDir()
}

// startRedisMediaWorker runs the background worker listening to Redis list media:jobs:queue
func startRedisMediaWorker() {
	redisClient = initRedis()
	ctx := context.Background()
	queueKey := "media:jobs:queue"

	log.Printf("⚡ [MEDIA_WORKER] Go Media Worker listening to Redis queue: '%s'...", queueKey)

	for {
		// BLPop blocks for up to 5 seconds waiting for a new job
		result, err := redisClient.BLPop(ctx, 5*time.Second, queueKey).Result()
		if err != nil {
			if err == redis.Nil {
				continue // Timeout, keep waiting
			}
			// Connection error: backoff and retry
			time.Sleep(2 * time.Second)
			continue
		}

		if len(result) < 2 {
			continue
		}

		jobJSON := result[1]
		var job MediaJob
		if err := json.Unmarshal([]byte(jobJSON), &job); err != nil {
			log.Printf("❌ [MEDIA_WORKER] Failed to parse job JSON: %v", err)
			continue
		}

		// Process concurrently using goroutine
		go processMediaJob(ctx, redisClient, job)
	}
}
