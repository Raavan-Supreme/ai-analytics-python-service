package com.ai.analytics.backend.model;

import jakarta.persistence.*;
import lombok.*;

import java.time.Instant;

@Entity
@Table(name = "dashboards")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class Dashboard {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    private User owner;

    @Column(nullable = false)
    private String name;

    @Lob
    @Column(nullable = false)
    private String configJson;

    private Instant createdAt;

    private Instant updatedAt;
}
