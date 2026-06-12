package com.ai.analytics.backend.controller;

import com.ai.analytics.backend.model.AnalysisSession;
import com.ai.analytics.backend.model.Dashboard;
import com.ai.analytics.backend.model.FileRelationship;
import com.ai.analytics.backend.model.QueryHistory;
import com.ai.analytics.backend.model.UploadedFile;
import com.ai.analytics.backend.model.User;
import com.ai.analytics.backend.repository.AnalysisSessionRepository;
import com.ai.analytics.backend.repository.DashboardRepository;
import com.ai.analytics.backend.repository.FileRelationshipRepository;
import com.ai.analytics.backend.repository.QueryHistoryRepository;
import com.ai.analytics.backend.repository.UploadedFileRepository;
import com.ai.analytics.backend.repository.UserRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.data.domain.PageRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/query")
@CrossOrigin(origins = "*")
public class QueryController {

    private final UserRepository userRepository;
    private final UploadedFileRepository fileRepository;
    private final AnalysisSessionRepository sessionRepository;
    private final FileRelationshipRepository relationshipRepository;
    private final QueryHistoryRepository queryHistoryRepository;
    private final DashboardRepository dashboardRepository;
    private final RestTemplate restTemplate = new RestTemplate();
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${app.python-service-base-url}")
    private String pythonServiceBaseUrl;

    public QueryController(UserRepository userRepository,
                           UploadedFileRepository fileRepository,
                           AnalysisSessionRepository sessionRepository,
                           FileRelationshipRepository relationshipRepository,
                           QueryHistoryRepository queryHistoryRepository,
                           DashboardRepository dashboardRepository) {
        this.userRepository = userRepository;
        this.fileRepository = fileRepository;
        this.sessionRepository = sessionRepository;
        this.relationshipRepository = relationshipRepository;
        this.queryHistoryRepository = queryHistoryRepository;
        this.dashboardRepository = dashboardRepository;
    }

    public record QueryRequest(String email,
                               Long fileId,
                               List<Long> fileIds,
                               List<Long> relationshipIds,
                               String question,
                               String sheetName,
                               String chartType) {}

    public record CreateRelationshipRequest(String email,
                                            Long leftFileId,
                                            Long rightFileId,
                                            String leftKey,
                                            String rightKey,
                                            String joinType) {}

    public record CreateDashboardRequest(String email, String name, Map<String, Object> config) {}

    private User getUser(String email) {
        return userRepository.findByEmail(email).orElseThrow();
    }

    private List<UploadedFile> resolveRequestedFiles(User user, QueryRequest req) {
        List<Long> ids = new ArrayList<>();
        if (req.fileId() != null) {
            ids.add(req.fileId());
        }
        if (req.fileIds() != null) {
            ids.addAll(req.fileIds());
        }
        if (ids.isEmpty()) {
            throw new IllegalArgumentException("Provide at least one fileId or fileIds");
        }

        List<Long> distinctIds = ids.stream().distinct().toList();
        List<UploadedFile> files = distinctIds.stream()
            .map(id -> fileRepository.findById(id).orElseThrow())
            .collect(Collectors.toList());

        boolean allOwned = files.stream().allMatch(file -> file.getOwner().getId().equals(user.getId()));
        if (!allOwned) {
            throw new IllegalArgumentException("One or more files do not belong to user");
        }

        return files;
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new RuntimeException(e);
        }
    }

    @PostMapping
    public ResponseEntity<?> query(@RequestBody QueryRequest req) {
        try {
            User user = getUser(req.email());
            List<UploadedFile> selectedFiles = resolveRequestedFiles(user, req);

            AnalysisSession session = AnalysisSession.builder()
                    .owner(user)
                    .file(selectedFiles.size() == 1 ? selectedFiles.get(0) : null)
                    .name("Session " + Instant.now())
                    .createdAt(Instant.now())
                    .build();
            sessionRepository.save(session);

            Map<String, Object> payload = new HashMap<>();
            payload.put("sessionId", session.getId());
            payload.put("filePath", selectedFiles.get(0).getStoredPath());
            payload.put("filePaths", selectedFiles.stream().map(UploadedFile::getStoredPath).toList());
            payload.put("question", req.question());
            payload.put("chartType", req.chartType() == null ? "auto" : req.chartType());
            if (req.sheetName() != null && !req.sheetName().isBlank()) {
                payload.put("sheetName", req.sheetName());
            }

            if (req.relationshipIds() != null && !req.relationshipIds().isEmpty()) {
                List<Map<String, Object>> relationships = req.relationshipIds().stream()
                        .distinct()
                        .map(id -> relationshipRepository.findById(id).orElseThrow())
                        .filter(r -> r.getOwner().getId().equals(user.getId()))
                    .map(r -> Map.<String, Object>of(
                                "leftPath", r.getLeftFile().getStoredPath(),
                                "rightPath", r.getRightFile().getStoredPath(),
                                "leftKey", r.getLeftKey(),
                                "rightKey", r.getRightKey(),
                                "joinType", r.getJoinType()
                        ))
                        .toList();
                payload.put("relationships", relationships);
            }

            String url = pythonServiceBaseUrl + "/nl-query";
            Map<String, Object> resp = restTemplate.postForObject(url, payload, Map.class);

            String chartDownloadUrl = null;
            if (resp != null && resp.get("chart") instanceof Map<?, ?> chartMap) {
                Object download = chartMap.get("downloadUrl");
                if (download instanceof String downloadStr) {
                    if (downloadStr.startsWith("/")) {
                        chartDownloadUrl = pythonServiceBaseUrl + downloadStr;
                        ((Map<String, Object>) chartMap).put("downloadUrl", chartDownloadUrl);
                    } else {
                        chartDownloadUrl = downloadStr;
                    }
                }
            }

            if (resp != null && resp.get("charts") instanceof List<?> chartList) {
                for (Object chartObj : chartList) {
                    if (chartObj instanceof Map<?, ?> rawMap) {
                        Object download = rawMap.get("downloadUrl");
                        if (download instanceof String downloadStr && downloadStr.startsWith("/")) {
                            ((Map<String, Object>) rawMap).put("downloadUrl", pythonServiceBaseUrl + downloadStr);
                        }
                    }
                }
            }

            QueryHistory history = QueryHistory.builder()
                    .owner(user)
                    .session(session)
                    .question(req.question())
                    .resultPreviewJson(toJson(resp == null ? Map.of() : resp.getOrDefault("rows", List.of())))
                    .summary(resp == null ? "" : String.valueOf(resp.getOrDefault("summary", "")))
                    .status("success")
                    .chartDownloadUrl(chartDownloadUrl)
                    .createdAt(Instant.now())
                    .build();
            queryHistoryRepository.save(history);

            if (resp == null) {
                resp = new HashMap<>();
            }
            resp.put("queryId", history.getId());
            return ResponseEntity.ok(resp);
        } catch (HttpStatusCodeException e) {
            return ResponseEntity.status(502).body(Map.of(
                    "error", "Python service query failed",
                    "details", e.getResponseBodyAsString()
            ));
        } catch (Exception e) {
            return ResponseEntity.status(500).body(Map.of(
                    "error", "Query processing failed",
                    "details", e.getMessage()
            ));
        }
    }

    @PostMapping("/relationships")
    public ResponseEntity<?> createRelationship(@RequestBody CreateRelationshipRequest req) {
        User user = getUser(req.email());
        UploadedFile left = fileRepository.findById(req.leftFileId()).orElseThrow();
        UploadedFile right = fileRepository.findById(req.rightFileId()).orElseThrow();

        if (!left.getOwner().getId().equals(user.getId()) || !right.getOwner().getId().equals(user.getId())) {
            return ResponseEntity.badRequest().body(Map.of("error", "Files do not belong to user"));
        }

        String joinType = req.joinType() == null ? "inner" : req.joinType();
        if (!List.of("inner", "left", "right", "outer").contains(joinType)) {
            return ResponseEntity.badRequest().body(Map.of("error", "Invalid joinType"));
        }

        FileRelationship relationship = FileRelationship.builder()
                .owner(user)
                .leftFile(left)
                .rightFile(right)
                .leftKey(req.leftKey())
                .rightKey(req.rightKey())
                .joinType(joinType)
                .createdAt(Instant.now())
                .build();

        relationshipRepository.save(relationship);
        return ResponseEntity.ok(Map.of(
                "relationshipId", relationship.getId(),
                "leftFileId", left.getId(),
                "rightFileId", right.getId(),
                "leftKey", relationship.getLeftKey(),
                "rightKey", relationship.getRightKey(),
                "joinType", relationship.getJoinType()
        ));
    }

    @GetMapping("/relationships")
    public List<Map<String, Object>> listRelationships(@RequestParam("email") String email) {
        User user = getUser(email);
        return relationshipRepository.findByOwner(user).stream().map(r -> Map.<String, Object>of(
                "relationshipId", r.getId(),
                "leftFileId", r.getLeftFile().getId(),
                "rightFileId", r.getRightFile().getId(),
                "leftName", r.getLeftFile().getOriginalName(),
                "rightName", r.getRightFile().getOriginalName(),
                "leftKey", r.getLeftKey(),
                "rightKey", r.getRightKey(),
                "joinType", r.getJoinType(),
                "createdAt", r.getCreatedAt()
        )).toList();
    }

    @GetMapping("/history")
    public List<Map<String, Object>> history(@RequestParam("email") String email,
                                             @RequestParam(defaultValue = "50") int limit) {
        User user = getUser(email);
        return queryHistoryRepository.findByOwnerOrderByCreatedAtDesc(user, PageRequest.of(0, limit)).stream()
            .map(item -> {
                Map<String, Object> row = new java.util.LinkedHashMap<>();
                AnalysisSession session = item.getSession();
                Long fileId = (session != null && session.getFile() != null) ? session.getFile().getId() : null;
                row.put("queryId", item.getId());
                row.put("sessionId", session == null ? null : session.getId());
                row.put("fileId", fileId);
                row.put("question", item.getQuestion());
                row.put("summary", item.getSummary());
                row.put("status", item.getStatus());
                row.put("chartDownloadUrl", item.getChartDownloadUrl());
                row.put("createdAt", item.getCreatedAt());
                return row;
            })
                .toList();
    }

    @PostMapping("/dashboards")
    public ResponseEntity<?> createDashboard(@RequestBody CreateDashboardRequest req) {
        User user = getUser(req.email());
        Instant now = Instant.now();

        Dashboard dashboard = Dashboard.builder()
                .owner(user)
                .name(req.name())
                .configJson(toJson(req.config() == null ? Map.of() : req.config()))
                .createdAt(now)
                .updatedAt(now)
                .build();

        dashboardRepository.save(dashboard);

        return ResponseEntity.ok(Map.of(
                "dashboardId", dashboard.getId(),
                "name", dashboard.getName(),
                "config", req.config(),
                "createdAt", dashboard.getCreatedAt()
        ));
    }

    @GetMapping("/dashboards")
    public List<Map<String, Object>> listDashboards(@RequestParam("email") String email) {
        User user = getUser(email);
        return dashboardRepository.findByOwnerOrderByUpdatedAtDesc(user).stream()
            .map(d -> Map.<String, Object>of(
                        "dashboardId", d.getId(),
                        "name", d.getName(),
                        "configJson", d.getConfigJson(),
                        "createdAt", d.getCreatedAt(),
                        "updatedAt", d.getUpdatedAt()
                ))
                .toList();
    }
}
