package com.ai.analytics.backend.model;

import jakarta.persistence.*;
import lombok.*;

import java.time.Instant;

@Entity
@Table(name = "query_history")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class QueryHistory {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    private User owner;

    @ManyToOne
    private AnalysisSession session;

    @Column(nullable = false, length = 2000)
    private String question;

    @Column(columnDefinition = "TEXT")
    private String resultPreviewJson;

    @Column(columnDefinition = "TEXT")
    private String summary;


    @Column(length = 64)
    private String status;

    @Column(length = 512)
    private String chartDownloadUrl;

    private Instant createdAt;
}
