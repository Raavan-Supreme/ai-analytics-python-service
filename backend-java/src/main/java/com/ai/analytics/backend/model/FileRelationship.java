package com.ai.analytics.backend.model;

import jakarta.persistence.*;
import lombok.*;

import java.time.Instant;

@Entity
@Table(name = "file_relationships")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class FileRelationship {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    private User owner;

    @ManyToOne(optional = false)
    private UploadedFile leftFile;

    @ManyToOne(optional = false)
    private UploadedFile rightFile;

    @Column(nullable = false)
    private String leftKey;

    @Column(nullable = false)
    private String rightKey;

    @Column(nullable = false)
    private String joinType;

    private Instant createdAt;
}
