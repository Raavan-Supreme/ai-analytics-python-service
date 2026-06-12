package com.ai.analytics.backend.controller;

import com.ai.analytics.backend.model.UploadedFile;
import com.ai.analytics.backend.model.User;
import com.ai.analytics.backend.repository.UploadedFileRepository;
import com.ai.analytics.backend.repository.UserRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.util.UriComponentsBuilder;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/files")
@CrossOrigin(origins = "*")
public class FileController {

    private final UploadedFileRepository fileRepository;
    private final UserRepository userRepository;
    private final RestTemplate restTemplate = new RestTemplate();

    @Value("${app.storage-root}")
    private String storageRoot;

    @Value("${app.python-service-base-url}")
    private String pythonServiceBaseUrl;

    public FileController(UploadedFileRepository fileRepository, UserRepository userRepository) {
        this.fileRepository = fileRepository;
        this.userRepository = userRepository;
    }

    private UploadedFile storeSingleFile(MultipartFile multipartFile, User user) throws IOException {
        File dir = new File(storageRoot).getAbsoluteFile();
        if (!dir.exists()) {
            dir.mkdirs();
        }

        String storedName = System.currentTimeMillis() + "_" + multipartFile.getOriginalFilename();
        File dest = new File(dir, storedName);
        multipartFile.transferTo(dest.toPath());

        UploadedFile file = UploadedFile.builder()
                .owner(user)
                .originalName(multipartFile.getOriginalFilename())
                .storedPath(dest.getAbsolutePath())
                .uploadedAt(Instant.now())
                .build();

        return fileRepository.save(file);
    }

    @PostMapping("/upload")
    public ResponseEntity<?> upload(@RequestParam("file") MultipartFile multipartFile,
                                    @RequestParam("email") String email) throws IOException {
        User user = userRepository.findByEmail(email).orElseThrow();
        UploadedFile file = storeSingleFile(multipartFile, user);

        return ResponseEntity.ok(Map.of(
                "id", file.getId(),
                "originalName", file.getOriginalName()
        ));
    }

        @PostMapping("/upload-multiple")
        public ResponseEntity<?> uploadMultiple(@RequestParam("files") List<MultipartFile> files,
                            @RequestParam("email") String email) throws IOException {
        User user = userRepository.findByEmail(email).orElseThrow();
        List<Map<String, Object>> uploaded = new ArrayList<>();

        for (MultipartFile multipartFile : files) {
            UploadedFile saved = storeSingleFile(multipartFile, user);
            uploaded.add(Map.<String, Object>of(
                "id", saved.getId(),
                "originalName", saved.getOriginalName()
            ));
        }

        return ResponseEntity.ok(Map.of(
            "files", uploaded,
            "count", uploaded.size()
        ));
        }

    @GetMapping
        public List<Map<String, Object>> list(@RequestParam("email") String email) {
            User user = userRepository.findByEmail(email).orElseThrow();
            return fileRepository.findByOwner(user).stream().map(file -> Map.<String, Object>of(
            "id", file.getId(),
            "originalName", file.getOriginalName(),
            "uploadedAt", file.getUploadedAt(),
            "storedPath", file.getStoredPath()
        )).toList();
        }

        @GetMapping("/{fileId}/preview")
        public ResponseEntity<?> preview(@PathVariable Long fileId,
                         @RequestParam("email") String email,
                         @RequestParam(defaultValue = "20") int limit,
                         @RequestParam(required = false) String sheetName) {
        User user = userRepository.findByEmail(email).orElseThrow();
        UploadedFile file = fileRepository.findById(fileId)
            .filter(f -> f.getOwner().getId().equals(user.getId()))
            .orElseThrow();

        UriComponentsBuilder builder = UriComponentsBuilder.fromHttpUrl(pythonServiceBaseUrl + "/preview-file")
            .queryParam("filePath", file.getStoredPath())
            .queryParam("limit", limit);
        if (sheetName != null && !sheetName.isBlank()) {
            builder.queryParam("sheetName", sheetName);
        }
        String url = builder.toUriString();
        Map<?, ?> response = restTemplate.postForObject(url, null, Map.class);
        return ResponseEntity.ok(response);
    }
}
